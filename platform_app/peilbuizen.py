"""
Peilbuizen → Kaart: every groundwater series on a map; click one, or search, to see its head.

All series Databricks has (data_validation's object_data.csv), not only the ones in a cross
section. One marker per location: the filters of one well share a spot, and a click on it
offers each filter. Series without a position (not linked to hl_cs_objecten; see Data
validation → Data sources) are not on the map but are found by search.

The head is the daily mean with the daily min–max, live from the SQL warehouse when
`DATABRICKS_WAREHOUSE_ID` is set, otherwise from the hourly export in the repo. If the
series has been reviewed, the validated line (approved hours, correction applied) is drawn
too. The chosen series is in the URL (`?reeks=<filename>`).
"""

from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from pandas import DataFrame

from local_validation import data_io, stores
from modules import databricks_live
from review_app import common
from review_app.map_page import BASEMAPS, VENDOR_COLOURS

from . import peilbuizen_data as data
from .stijghoogtes import HULPLIJN_KLEUR, REEKS_KLEUR, _tijdas

REEKS_PARAM = "reeks"
GEVALIDEERD_KLEUR = "#1baf7a"
KAART_HOOGTE = 560


@st.cache_data(ttl=600, show_spinner="Stijghoogte ophalen uit Databricks…", max_entries=50)
def _dag_live(meetreeks_id: str) -> DataFrame:
    return databricks_live.dag_voor_reeksen([meetreeks_id])


@st.cache_data(show_spinner=False, max_entries=50)
def _dag_export(filename: str) -> DataFrame:
    return data.dag_uit_export(data_io.series_csv(common.settings(), filename))


def _dag(rij) -> tuple[DataFrame, str]:
    """Daily values and where they came from."""
    if databricks_live.beschikbaar():
        try:
            return _dag_live(rij.meetreeks_id), "live uit Databricks"
        except Exception as fout:  # noqa: BLE001 - fall back to the export
            st.warning(f"Live ophalen mislukte, dus dit is de export. ({str(fout)[:200]})")
    return _dag_export(rij.filename), "uit de export in de repo (uurgemiddelden)"


def _kies(filename: str | None) -> None:
    if filename:
        st.query_params[REEKS_PARAM] = filename
    else:
        st.query_params.pop(REEKS_PARAM, None)
    st.session_state["pb_versie"] = st.session_state.get("pb_versie", 0) + 1
    st.rerun()


def _kaart(meta: DataFrame, basemap: str, gekozen_loc: str | None) -> go.Figure:
    locs = data.locaties(meta)
    fig = go.Figure()
    for vendor, sub in locs.groupby("vendor", sort=True):
        fig.add_trace(go.Scattermap(
            lon=sub["lon"], lat=sub["lat"], mode="markers", name=str(vendor),
            marker={"size": (8 + 2 * sub["n_reeksen"].clip(upper=5)).tolist(),
                    "color": VENDOR_COLOURS.get(vendor, "#7F7F7F"), "opacity": 0.85},
            customdata=sub[["loc"]].to_numpy(), text=sub["hover"], hovertemplate="%{text}<extra></extra>"))
    hier = locs[locs["loc"] == gekozen_loc]
    if not hier.empty:
        fig.add_trace(go.Scattermap(lon=hier["lon"], lat=hier["lat"], mode="markers", name="gekozen",
                                    marker={"size": 22, "color": "#e34948", "opacity": 0.55}, hoverinfo="skip"))
    midden = (hier if not hier.empty else locs)
    lay = {"center": {"lon": float(midden["lon"].mean()), "lat": float(midden["lat"].mean())},
           "zoom": 12 if not hier.empty else 8.6}
    tiles = BASEMAPS[basemap]
    if tiles:
        lay.update(style="white-bg", layers=[{"below": "traces", "sourcetype": "raster", "source": [tiles],
                                              "sourceattribution": "© PDOK / Kadaster"}])
    else:
        lay["style"] = "open-street-map"
    fig.update_layout(map=lay, height=KAART_HOOGTE, margin={"l": 0, "r": 0, "t": 0, "b": 0}, uirevision=gekozen_loc,
                      legend={"orientation": "h", "y": 1.0, "x": 0.0, "bgcolor": "rgba(255,255,255,0.8)"})
    return fig


def _geklikt(gebeurtenis) -> str | None:
    for punt in (gebeurtenis or {}).get("selection", {}).get("points", []):
        cd = punt.get("customdata")
        if cd:
            return cd[0] if isinstance(cd, (list, tuple)) else cd
    return None


def _niveau(fig: go.Figure, waarde, naam: str, streep: str) -> None:
    if pd.notna(waarde):
        fig.add_hline(y=float(waarde), line={"color": HULPLIJN_KLEUR, "width": 1, "dash": streep},
                      annotation_text=f"{naam} {float(waarde):.2f}", annotation_position="top left",
                      annotation_font_color=HULPLIJN_KLEUR)


def _dwarsprofielen(meetreeks_id: str) -> DataFrame:
    """The cross sections this series is used in (Boxplots' choice per well); empty if none."""
    try:
        from . import bron as bron_module
        from .stijghoogtes import _catalogus
        b = bron_module.bron()
        cat = _catalogus(bron_module.versie(b), b)
    except Exception:  # noqa: BLE001 - the links are a convenience
        return DataFrame()
    return cat[cat["meetreeks_id"] == meetreeks_id].drop_duplicates(["project", "dwarsprofiel"])


def _toon_reeks(rij, meta: DataFrame) -> None:
    from . import nav
    from .stijghoogtes import PROFIEL_PARAM, PROJECT_PARAM

    st.subheader(data.label(rij))
    st.caption(" · ".join([
        f"`{rij.filename}`", f"meetlocatie {rij.meetlocatie_code if isinstance(rij.meetlocatie_code, str) else '–'}",
        f"filter {common.fmt(rij.begin_filter)} / {common.fmt(rij.eind_filter)} m NAP",
        f"bovenkant peilbuis {common.fmt(rij.bovenkant_peilbuis)} m NAP",
        "op de kaart" if rij.has_location else "geen ligging (niet gekoppeld aan hl_cs_objecten)"]))

    if rij.has_location:
        hier = data.reeksen_op_locatie(meta, data.locatie_sleutel(meta.loc[[rij.Index]]).iloc[0])
        if len(hier) > 1:
            namen = hier["filename"].tolist()
            ander = st.segmented_control(
                f"{len(hier)} filters op deze locatie", namen, default=rij.filename,
                format_func=lambda f: f"{hier.set_index('filename').at[f, 'code']}", key=f"pb_filters_{rij.filename}")
            if ander and ander != rij.filename:
                _kies(ander)

    dag, herkomst = _dag(rij)
    store = stores.load_saved(common.settings(), rij.filename)
    gevalideerd = data.dag_gevalideerd(store)

    o1, o2, o3 = st.columns(3)
    band = o1.toggle("Dagelijkse min–max", value=True, disabled=dag.empty)
    niveaus = o2.toggle("Filter en bovenkant peilbuis", value=True)
    toon_val = o3.toggle("Gevalideerd (goedgekeurd, met correctie)", value=not gevalideerd.empty,
                         disabled=gevalideerd.empty,
                         help="De goedgekeurde uren uit de review, met de correctie toegepast"
                              if not gevalideerd.empty else "Deze reeks is nog niet gereviewd.")
    if dag.empty:
        st.info(f"Geen metingen voor deze reeks ({herkomst}).")
    else:
        m = st.columns(4)
        m[0].metric("Dagen", f"{len(dag)}", help=f"{dag['datum'].min():%d-%m-%Y} t/m {dag['datum'].max():%d-%m-%Y}")
        m[1].metric("Gemiddeld", f"{dag['gem'].mean():.2f} m NAP")
        m[2].metric("Laagste daggemiddelde", f"{dag['gem'].min():.2f} m NAP")
        m[3].metric("Hoogste daggemiddelde", f"{dag['gem'].max():.2f} m NAP")

        fig = go.Figure()
        if band and {"min", "max"} <= set(dag):
            fig.add_trace(go.Scatter(x=dag["datum"], y=dag["min"], line={"width": 0}, hoverinfo="skip",
                                     showlegend=False))
            fig.add_trace(go.Scatter(x=dag["datum"], y=dag["max"], line={"width": 0}, fill="tonexty",
                                     fillcolor="rgba(42,120,214,0.18)", name="dagelijkse min–max", hoverinfo="skip"))
        fig.add_trace(go.Scatter(x=dag["datum"], y=dag["gem"], name="daggemiddelde (ruw)",
                                 line={"color": REEKS_KLEUR, "width": 2},
                                 hovertemplate="%{x|%d-%m-%Y}: %{y:.3f} m NAP<extra></extra>"))
        if toon_val and not gevalideerd.empty:
            # Reindexed on every day, so days without approved hours break the line.
            volledig = gevalideerd.set_index("datum")["gem"].asfreq("D")
            fig.add_trace(go.Scatter(x=volledig.index, y=volledig.values, name="gevalideerd",
                                     line={"color": GEVALIDEERD_KLEUR, "width": 2},
                                     hovertemplate="%{x|%d-%m-%Y}: %{y:.3f} m NAP gevalideerd<extra></extra>"))
        if niveaus:
            _niveau(fig, rij.bovenkant_peilbuis, "bovenkant peilbuis", "dot")
            _niveau(fig, rij.begin_filter, "bovenkant filter", "dash")
            _niveau(fig, rij.eind_filter, "onderkant filter", "dash")
        _tijdas(fig)
        fig.update_layout(hovermode="x unified", title_text=str(rij.code))
        st.plotly_chart(fig, config={"displaylogo": False})
        st.caption(f"Stijghoogte {herkomst}"
                   + (f" · gevalideerd: {int(gevalideerd['n'].sum())} goedgekeurde uren" if not gevalideerd.empty else ""))

    links = st.columns(3)
    if links[0].button("Open in Review →", width="stretch"):
        common.open_in_review(rij.filename)
    profielen = _dwarsprofielen(rij.meetreeks_id)
    for i, p in enumerate(profielen.head(2).itertuples(), start=1):
        links[i].page_link(nav.PER_DWARSPROFIEL, label=f"Dwarsprofiel {p.dwarsprofiel}", icon=":material/view_column:",
                           query_params={PROJECT_PARAM: p.project, PROFIEL_PARAM: p.dwarsprofiel})
    if not dag.empty:
        st.download_button("Download daggemiddelden als CSV", dag.to_csv(index=False, float_format="%.4f").encode("utf-8"),
                           file_name=f"stijghoogte_{rij.filename}.csv", mime="text/csv")


def render_kaart() -> None:
    st.title("Peilbuizen")
    meta = common.object_data()
    if meta.empty:
        st.info("Geen meetreeksen in data_validation/input_output/output_databricks/metadata/object_data.csv.")
        return
    n_op = int(meta["has_location"].sum())
    st.caption(f"{len(meta)} meetreeksen · {n_op} op de kaart · {len(meta) - n_op} zonder ligging (wel te zoeken) · "
               "klik op een locatie of zoek een reeks")

    gevraagd = st.query_params.get(REEKS_PARAM)
    rij_df = meta[meta["filename"] == gevraagd]
    rij = next(rij_df.itertuples()) if not rij_df.empty else None
    gekozen_loc = data.locatie_sleutel(rij_df).iloc[0] if rij is not None and rij.has_location else None
    nr = st.session_state.get("pb_versie", 0)

    labels = {r.filename: data.label(r) + ("" if r.has_location else " (geen ligging)")
              for r in meta.sort_values("code").itertuples()}
    z1, z2 = st.columns([3, 1])
    gezocht = z1.selectbox("Zoek een meetreeks", list(labels), index=list(labels).index(gevraagd) if rij else None,
                           format_func=labels.get, placeholder="code, locatienaam of leverancier",
                           key=f"pb_zoek_{nr}")
    basemap = z2.selectbox("Achtergrond", list(BASEMAPS), key="pb_basemap")
    if gezocht and gezocht != gevraagd:
        _kies(gezocht)

    gebeurtenis = st.plotly_chart(_kaart(meta, basemap, gekozen_loc), on_select="rerun", selection_mode="points",
                                  key=f"pb_kaart_{nr}", config={"scrollZoom": True, "displaylogo": False})
    geklikt = _geklikt(gebeurtenis)
    if geklikt and geklikt != gekozen_loc:
        _kies(data.reeksen_op_locatie(meta, geklikt)["filename"].iloc[0])

    if rij is None:
        if gevraagd:
            st.warning(f"Meetreeks {gevraagd} bestaat niet.")
        st.info("Klik op een locatie op de kaart, of zoek een meetreeks.")
        return
    _toon_reeks(rij, meta)
