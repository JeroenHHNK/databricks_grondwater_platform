"""
The pages of the central app, defined once so pages can link to each other
(`st.page_link` / `st.switch_page` need the same Page object that `st.navigation` got).
"""

from __future__ import annotations

from pathlib import Path

import streamlit as st

from review_app import nav as validation_nav

from . import pastas_pagina, peilbuizen, refresh, stijghoogtes

ROOT = Path(__file__).resolve().parents[1]

BOXPLOTS = st.Page(ROOT / "boxplots" / "app.py", title="Dwarsprofielen", icon=":material/stacked_line_chart:",
                   url_path="boxplots", default=True)

KAART = st.Page(peilbuizen.render_kaart, title="Kaart", icon=":material/map:", url_path="peilbuizen-kaart")
PER_DWARSPROFIEL = st.Page(stijghoogtes.render_dwarsprofiel, title="Per dwarsprofiel",
                           icon=":material/view_column:", url_path="stijghoogtes-dwarsprofiel")
ZONE = st.Page(stijghoogtes.render_zone, title="Zone vergelijken", icon=":material/compare_arrows:",
               url_path="stijghoogtes-zone")

MODELLEN = st.Page(pastas_pagina.render_modellen, title="Modellen", icon=":material/model_training:",
                   url_path="pastas-modellen")
VERGELIJKEN = st.Page(pastas_pagina.render_vergelijken, title="Gevalideerd vs ongevalideerd",
                      icon=":material/compare:", url_path="pastas-vergelijken")

REFRESH = st.Page(refresh.render, title="Refresh data", icon=":material/sync:", url_path="refresh-data")

SECTIES: dict[str, list] = {
    "Boxplots": [BOXPLOTS],
    "Peilbuizen": [KAART, PER_DWARSPROFIEL, ZONE],
    "Pastas": [MODELLEN, VERGELIJKEN],
    "Data validation": validation_nav.PAGES,
    "Data": [REFRESH],
}
