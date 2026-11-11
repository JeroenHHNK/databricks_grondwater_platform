"""
Two pages of the Peilbuizen section: groundwater heads per cross section and per zone.

On the same data as Boxplots (daily means of the series Boxplots picks per well; live when
DATABRICKS_WAREHOUSE_ID is set, otherwise the export):

- Per dwarsprofiel: all wells of one cross section, coloured by zone;
- Zone vergelijken: one zone (e.g. BIKR) across cross sections.

The section's first page, Kaart (one series at a time, every series Databricks has), is in
peilbuizen.py. The chosen cross section or zone is in the URL, so Boxplots can link here and
a view can be shared.
"""

from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from pandas import DataFrame

from modules import databricks_reeksen, workbook_lader

from . import bron as bron_module
from . import stijghoogtes_data as data

# Categorical slots in fixed order (the validated reference palette), one per zone from
# outside to inside; BIRK shares BIKR's colour. Grey for wells without a zone token.
ZONE_KLEUR: dict[str, str] = {
    "BUTAL": "#6250d6", "BUT": "#6250d6", "BUKR": "#2a78d6", "BIKR": "#eb6834",
    "BIRK": "#eb6834", "KRBIB": "#1baf7a", "BIB": "#eda100", "INBIB": "#e87ba4",
    "INST": "#e87ba4", "INSD": "#e87ba4", "BITA": "#008300", "BIT": "#008300",
    data.ONBEKEND: "#8a8984",
}
REEKS_KLEUR = "#2a78d6"
INVALIDE_KLEUR = "#e34948"
HULPLIJN_KLEUR = "#8a8984"
STREEP: dict[str, str] = {workbook_lader.HOOFD: "solid", "A": "dash", "B": "dot"}
PLOT_HOOGTE = 520

PROJECT_PARAM, PROFIEL_PARAM, PEILBUIS_PARAM, ZONE_PARAM = "project", "dwarsprofiel", "peilbuis", "zone"


@st.cache_data(show_spinner="Peilbuizen klaarzetten…", max_entries=10)
def _catalogus(versie: tuple[float, str], _bron: databricks_reeksen.Bron) -> DataFrame:
    return data.catalogus(_bron)


def _laad() -> tuple[databricks_reeksen.Bron, DataFrame] | None:
    try:
        b = bron_module.bron()
    except Exception as fout:  # noqa: BLE001 - live failed and there is no export
        st.error(f"Geen stijghoogtes: live ophalen mislukte en er is geen export ({fout}).")
        return None
    if b.fout:
        st.warning(f"Live ophalen uit Databricks mislukte, dus dit toont de export. ({b.fout})")
    return b, _catalogus(bron_module.versie(b), b)


def _tijdas(fig: go.Figure) -> None:
    fig.update_xaxes(
        rangeslider={"visible": True, "thickness": 0.06},
        rangeselector={"x": 1, "xanchor": "right", "y": 1.04, "yanchor": "bottom", "buttons": [
            {"count": 1, "label": "1 mnd", "step": "month", "stepmode": "backward"},
            {"count": 6, "label": "6 mnd", "step": "month", "stepmode": "backward"},
            {"count": 1, "label": "1 jaar", "step": "year", "stepmode": "backward"},
            {"step": "all", "label": "alles"},
        ]},
    )
    fig.update_yaxes(title="stijghoogte (m NAP)", showgrid=True, gridwidth=1)
    fig.update_layout(height=PLOT_HOOGTE, margin={"l": 10, "r": 10, "t": 60, "b": 10},
                      title={"x": 0, "xanchor": "left", "font": {"size": 15}},
                      legend={"orientation": "h", "y": -0.25})


def _download(tabel: DataFrame, naam: str) -> None:
    if not tabel.empty:
        st.download_button("Download als CSV", tabel.to_csv(float_format="%.4f").encode("utf-8"),
                           file_name=f"{naam}.csv", mime="text/csv")


def _kies(opties: list, standaard, label: str, format_func=str, key: str | None = None):
    index = opties.index(standaard) if standaard in opties else None
    return st.selectbox(label, opties, index=index, format_func=format_func, key=key,
                        placeholder="Kies of typ om te zoeken")


def _links(project: str, dwarsprofiel: str, zonder_profiel: bool = False) -> None:
    """Links to the other views of the same cross section."""
    from . import nav  # here, not at the top: nav imports this module

    kolommen = st.columns(3 if not zonder_profiel else 2)
    kolommen[0].page_link(nav.BOXPLOTS, label="Dwarsprofiel in Boxplots", icon=":material/stacked_line_chart:",
                          query_params={"profiel": dwarsprofiel})
    if not zonder_profiel:
        kolommen[1].page_link(nav.PER_DWARSPROFIEL, label="Alle peilbuizen van dit dwarsprofiel",
                              icon=":material/view_column:",
                              query_params={PROJECT_PARAM: project, PROFIEL_PARAM: dwarsprofiel})


# --- Per dwarsprofiel ---------------------------------------------------------------------------

def _lijnen(fig: go.Figure, cat: DataFrame, dag: DataFrame, naam, kleur, legendgroep=None) -> None:
    """One line per well in `cat`; `naam`, `kleur` and `legendgroep` map a catalogue row."""
    per_reeks = {mid: g for mid, g in dag.groupby("meetreeks_id")}
    getoond: set = set()
    for r in cat.itertuples():
        g = per_reeks.get(r.meetreeks_id)
        if g is None:
            continue
        groep = legendgroep(r) if legendgroep else None
        fig.add_trace(go.Scatter(
            x=g["datum"], y=g["gem"], name=naam(r) if not groep else groep,
            legendgroup=groep, showlegend=groep is None or groep not in getoond,
            line={"color": kleur(r), "width": 2 if groep is None else 1.5,
                  "dash": STREEP.get(r.diepte_niveau, "dashdot")},
            hovertemplate=f"{data.label(r)}<br>%{{x|%d-%m-%Y}}: %{{y:.3f}} m NAP<extra></extra>",
        ))
        getoond.add(groep)


def _peilbuis_tabel(cat: DataFrame, dag: DataFrame) -> None:
    tabel = data.samenvatting(cat, dag)[[
        "peilbuis", "zone", "diepte_niveau", "x_profiel", "bk_filter", "ok_filter",
        "van", "tot", "dagen", "gemiddeld", "laagste", "hoogste", "code",
    ]]
    st.dataframe(tabel, hide_index=True, width="stretch", column_config={
        "van": st.column_config.DateColumn("van", format="DD-MM-YYYY"),
        "tot": st.column_config.DateColumn("tot", format="DD-MM-YYYY"),
        "x_profiel": st.column_config.NumberColumn("x in profiel (m)", format="%.1f"),
    })


def render_dwarsprofiel() -> None:
    st.title("Stijghoogtes per dwarsprofiel")
    geladen = _laad()
    if geladen is None:
        return
    b, cat = geladen
    met_data = cat.dropna(subset=["meetreeks_id"])
    profielen = met_data[["project", "dwarsprofiel"]].drop_duplicates()
    opties = [f"{p}|{k}" for p, k in profielen.itertuples(index=False)]
    st.caption(f"{len(opties)} dwarsprofielen met stijghoogtes · {bron_module.herkomst(b)} · "
               "lijnstijl: doorgetrokken = hoofdprofiel, gestreept = A, gestippeld = B")

    gevraagd = f"{st.query_params.get(PROJECT_PARAM, '')}|{st.query_params.get(PROFIEL_PARAM, '')}"
    sleutel = _kies(opties, gevraagd, "Dwarsprofiel", lambda s: " · ".join(reversed(s.split("|", 1))))
    if sleutel is None:
        st.info("Kies een dwarsprofiel, bijvoorbeeld D11-DP_0079+6.")
        return
    project, dwarsprofiel = sleutel.split("|", 1)
    st.query_params.update({PROJECT_PARAM: project, PROFIEL_PARAM: dwarsprofiel})
    st.query_params.pop(PEILBUIS_PARAM, None)

    hier = cat[(cat["project"] == project) & (cat["dwarsprofiel"] == dwarsprofiel)]
    f1, f2 = st.columns(2)
    zones = f1.multiselect("Zones", data.zones_in(hier), default=data.zones_in(hier),
                           format_func=lambda z: f"{z} · {data.ZONE_UITLEG.get(z, '')}")
    niveaus_aanwezig = sorted(hier["diepte_niveau"].unique(), key=lambda n: (n != workbook_lader.HOOFD, n))
    niveaus = f2.multiselect("Diepte", niveaus_aanwezig, default=niveaus_aanwezig)
    gekozen = hier[hier["zone"].isin(zones) & hier["diepte_niveau"].isin(niveaus)]
    met = gekozen.dropna(subset=["meetreeks_id"])
    zonder = gekozen[gekozen["meetreeks_id"].isna()]

    dag = data.dagwaarden(b, met["meetreeks_id"].tolist())
    if met.empty:
        st.info("Geen peilbuizen met een meetreeks in deze selectie.")
    else:
        fig = go.Figure()
        _lijnen(fig, met, dag, naam=lambda r: f"{r.peilbuis} · {r.zone}"
                + ("" if r.diepte_niveau == workbook_lader.HOOFD else f" ({r.diepte_niveau})"),
                kleur=lambda r: ZONE_KLEUR.get(r.zone, ZONE_KLEUR[data.ONBEKEND]))
        _tijdas(fig)
        fig.update_layout(hovermode="x unified", title_text=f"{dwarsprofiel} · {project}")
        st.plotly_chart(fig, config={"displaylogo": False})
    if not zonder.empty:
        st.caption(f"Zonder meetreeks: {', '.join(zonder['peilbuis'] + ' (' + zonder['keuze'].fillna('') + ')')}")
    _peilbuis_tabel(gekozen, dag)
    _links(project, dwarsprofiel, zonder_profiel=True)
    _download(data.breed(dag, dict(zip(met["meetreeks_id"], met["peilbuis"]))),
              f"stijghoogtes_{project}_{dwarsprofiel}")


# --- Zone vergelijken ---------------------------------------------------------------------------

def render_zone() -> None:
    st.title("Eén zone over dwarsprofielen")
    geladen = _laad()
    if geladen is None:
        return
    b, cat = geladen
    met_data = cat.dropna(subset=["meetreeks_id"])
    st.caption(f"Stijghoogtes {bron_module.herkomst(b)} · kleur per project · hover voor peilbuis en dwarsprofiel")

    f1, f2, f3 = st.columns([1, 2, 1])
    zones = data.zones_in(met_data)
    zone = f1.selectbox("Zone", zones, index=zones.index(st.query_params.get(ZONE_PARAM, "BIKR"))
                        if st.query_params.get(ZONE_PARAM, "BIKR") in zones else 0,
                        format_func=lambda z: f"{z} · {data.ZONE_UITLEG.get(z, '')}")
    st.query_params[ZONE_PARAM] = zone
    in_zone = met_data[met_data["zone"] == zone]
    projecten = sorted(in_zone["project"].unique())
    gekozen_projecten = f2.multiselect("Projecten", projecten, default=projecten)
    niveaus_aanwezig = sorted(in_zone["diepte_niveau"].unique(), key=lambda n: (n != workbook_lader.HOOFD, n))
    niveaus = f3.multiselect("Diepte", niveaus_aanwezig,
                             default=[n for n in niveaus_aanwezig if n == workbook_lader.HOOFD] or niveaus_aanwezig)
    sel = in_zone[in_zone["project"].isin(gekozen_projecten) & in_zone["diepte_niveau"].isin(niveaus)]
    sel = sel.drop_duplicates(["project", "peilbuis"])
    if sel.empty:
        st.info("Geen peilbuizen in deze selectie.")
        return

    mediaan = st.toggle("Mediaan van de selectie", value=True,
                        help="Per dag de mediaan van de daggemiddelden van de getoonde peilbuizen.")
    dag = data.dagwaarden(b, sel["meetreeks_id"].tolist())
    # Colour follows the project (fixed order over all projects), so a filter never repaints a line.
    alle_projecten = sorted(cat["project"].unique())
    palet = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"]
    kleur_project = {p: palet[i] if i < len(palet) else HULPLIJN_KLEUR for i, p in enumerate(alle_projecten)}
    fig = go.Figure()
    _lijnen(fig, sel, dag, naam=lambda r: r.project, kleur=lambda r: kleur_project[r.project],
            legendgroep=lambda r: r.project)
    if mediaan and not dag.empty:
        med = dag.groupby("datum")["gem"].median()
        fig.add_trace(go.Scatter(x=med.index, y=med.values, name="mediaan", line={"color": "#0b0b0b", "width": 2.5},
                                 hovertemplate="mediaan %{x|%d-%m-%Y}: %{y:.3f} m NAP<extra></extra>"))
    _tijdas(fig)
    fig.update_layout(hovermode="closest",
                      title_text=f"{zone} · {len(sel)} peilbuizen in {sel['dwarsprofiel'].nunique()} dwarsprofielen")
    st.plotly_chart(fig, config={"displaylogo": False})

    tabel = data.samenvatting(sel, dag)[["project", "dwarsprofiel", "peilbuis", "diepte_niveau", "bk_filter",
                                          "ok_filter", "van", "tot", "dagen", "gemiddeld", "laagste", "hoogste"]]
    st.dataframe(tabel, hide_index=True, width="stretch", column_config={
        "van": st.column_config.DateColumn("van", format="DD-MM-YYYY"),
        "tot": st.column_config.DateColumn("tot", format="DD-MM-YYYY"),
    })
    namen = {r.meetreeks_id: f"{r.dwarsprofiel}_{r.peilbuis}" for r in sel.itertuples()}
    _download(data.breed(dag, namen), f"stijghoogtes_zone_{zone}")
