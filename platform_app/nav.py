"""
The pages of the central app, defined once so pages can link to each other
(`st.page_link` / `st.switch_page` need the same Page object that `st.navigation` got).
"""

from __future__ import annotations

from pathlib import Path

import streamlit as st

from review_app import nav as validation_nav

from . import refresh, stijghoogtes

ROOT = Path(__file__).resolve().parents[1]

BOXPLOTS = st.Page(ROOT / "boxplots" / "app.py", title="Dwarsprofielen", icon=":material/stacked_line_chart:",
                   url_path="boxplots", default=True)

EEN_PEILBUIS = st.Page(stijghoogtes.render_reeks, title="Eén peilbuis", icon=":material/show_chart:",
                       url_path="stijghoogtes-peilbuis")
PER_DWARSPROFIEL = st.Page(stijghoogtes.render_dwarsprofiel, title="Per dwarsprofiel",
                           icon=":material/view_column:", url_path="stijghoogtes-dwarsprofiel")
ZONE = st.Page(stijghoogtes.render_zone, title="Zone vergelijken", icon=":material/compare_arrows:",
               url_path="stijghoogtes-zone")

REFRESH = st.Page(refresh.render, title="Refresh data", icon=":material/sync:", url_path="refresh-data")

SECTIES: dict[str, list] = {
    "Boxplots": [BOXPLOTS],
    "Stijghoogtes": [EEN_PEILBUIS, PER_DWARSPROFIEL, ZONE],
    "Data validation": validation_nav.PAGES,
    "Data": [REFRESH],
}
