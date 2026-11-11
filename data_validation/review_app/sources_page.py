"""Data sources: per series, which fields come from the API and which from hl_cs_objecten.

`object_data.csv` (written by 01 + 02) joins two Databricks sources per series:

- **API** (blue): `gold_prd.hl_grondwaterpeilmeting.meetreeks` / `meting`, what the vendors
  (Fugro, Wiertsema) deliver: the series key, its code, filter depths, link columns.
- **hl_cs_objecten** (green): `gold_prd.hl_cs_objecten.meetlocatie`, the location registry:
  name, coordinates, compartment. Only filled when one of the four match keys found the
  series (see databricks_code/README.md, "The hl_cs_objecten match").

An empty green cell on a row is exactly what still has to be linked. Columns the pipeline
derives itself (filename, vendor, matched, …) stay uncoloured.
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from . import common

API_COLOUR = "rgba(42, 120, 214, 0.20)"
CS_COLOUR = "rgba(27, 175, 122, 0.24)"
MISSING_CS_COLOUR = "rgba(27, 175, 122, 0.07)"

API_COLUMNS = ["meetreeks_id", "code", "bronsysteem", "sensor_sleutel", "begin_filter", "eind_filter",
               "bovenkant_peilbuis", "eenheid", "meetlocatie_object_id", "meetlocatie_code", "object_id",
               "eigenschap_id", "validatie_status", "n_rows"]
CS_COLUMNS = ["cs_meetlocatie_id", "cs_code", "cs_naam", "compartiment_code", "compartiment_naam",
              "peilgebied_code", "meetreeks_actief", "rd_x", "rd_y", "lon", "lat"]
DERIVED_COLUMNS = ["filename", "vendor", "matched", "match_key", "cs_n_candidates"]
LINKED = "matched"

SHOW = {"All series": None, "Not linked to hl_cs_objecten": False, "Linked": True}


def _origin_table(meta: pd.DataFrame) -> pd.DataFrame:
    """Which source each column comes from, for the legend table."""
    rows = [(c, "API · hl_grondwaterpeilmeting") for c in API_COLUMNS if c in meta] + \
           [(c, "hl_cs_objecten.meetlocatie") for c in CS_COLUMNS if c in meta] + \
           [(c, "derived by the pipeline") for c in DERIVED_COLUMNS if c in meta]
    return pd.DataFrame(rows, columns=["column", "source"])


def _style(table: pd.DataFrame):
    api = [c for c in API_COLUMNS if c in table]
    cs = [c for c in CS_COLUMNS if c in table]

    def cs_cells(col: pd.Series) -> list[str]:
        return [f"background-color: {CS_COLOUR if pd.notna(v) and v != '' else MISSING_CS_COLOUR}" for v in col]

    styler = table.style.set_properties(subset=api, **{"background-color": API_COLOUR})
    styler = styler.apply(cs_cells, subset=cs)
    return styler.format(precision=2, na_rep="")


def render() -> None:
    st.title("Data sources")
    meta = common.object_data()
    linked = meta["matched"].eq(LINKED)

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Series", len(meta))
    c2.metric("Linked to hl_cs_objecten", int(linked.sum()), help="A match key found a meetlocatie")
    c3.metric("API only, still to link", int((~linked).sum()))
    c4.metric("Linked, several candidates", int((linked & (pd.to_numeric(meta["cs_n_candidates"], errors="coerce") > 1)).sum()),
              help="The match key found more than one meetlocatie; the lowest id was taken")

    st.markdown(
        f'<span style="background:{API_COLOUR};padding:2px 8px;border-radius:4px">blue: from the API '
        f'(hl_grondwaterpeilmeting.meetreeks / meting)</span> &nbsp; '
        f'<span style="background:{CS_COLOUR};padding:2px 8px;border-radius:4px">green: from '
        f'hl_cs_objecten.meetlocatie</span> &nbsp; '
        f'<span style="background:{MISSING_CS_COLOUR};padding:2px 8px;border-radius:4px">pale green, empty: '
        f'not linked yet</span>',
        unsafe_allow_html=True)

    f1, f2, f3 = st.columns([2, 2, 3])
    show = f1.selectbox("Show", list(SHOW))
    vendors = sorted(meta["vendor"].dropna().unique())
    pick_vendors = f2.multiselect("Vendor", vendors, default=vendors)
    query = f3.text_input("Search filename / code / meetlocatie code / sensor key", "")

    view = meta[meta["vendor"].isin(pick_vendors)]
    want = SHOW[show]
    if want is not None:
        view = view[view["matched"].eq(LINKED) == want]
    if query:
        hay = view[["filename", "code", "meetlocatie_code", "sensor_sleutel"]].astype(str).agg(" ".join, axis=1)
        view = view[hay.str.lower().str.contains(query.lower(), regex=False)]

    cols = [c for c in ["filename", "vendor", "matched", "match_key", *API_COLUMNS, *CS_COLUMNS, "cs_n_candidates"]
            if c in view]
    st.caption(f"{len(view)} series · one row per series in object_data.csv")
    st.dataframe(_style(view[cols].reset_index(drop=True)), hide_index=True, width="stretch", height=520)
    st.download_button("Download as CSV", view[cols].to_csv(index=False).encode("utf-8"),
                        file_name="data_sources.csv", mime="text/csv")

    with st.expander("Which match key linked them"):
        keys = meta.assign(match_key=meta["match_key"].fillna("— not linked")).groupby(
            ["vendor", "match_key"]).size().rename("series").reset_index()
        st.dataframe(keys, hide_index=True, width="stretch")
        st.caption("Keys are tried in order: meetlocatie_object_id → meetlocatie_code → trim(code) → object_id. "
                   "A series with none of them filled in, or with values that hl_cs_objecten does not know, "
                   "stays API only.")
    with st.expander("Which column comes from where"):
        st.dataframe(_origin_table(meta), hide_index=True, width="stretch")
