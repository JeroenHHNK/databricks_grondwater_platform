"""
Central Streamlit app (Databricks App) for the groundwater platform.

Sections in the navigation pane (defined in platform_app/nav.py):

- Boxplots: cross-sections with soil build-up, wells and heads (boxplots/app.py).
- Peilbuizen: every series on a map, and heads per cross section and per zone
  (platform_app/peilbuizen.py, platform_app/stijghoogtes.py).
- Pastas: the fitted models, their return periods and the family pick (platform_app/pastas_pagina.py).
- Data validation: map, point-by-point review, progress and data sources (data_validation/review_app/).
- Data: Refresh data, the one place to reload data and check live access (platform_app/refresh.py).

Each project keeps its own folder, code and data; this file only puts them side by side.
Run from the repo root:

    uv run streamlit run app.py

The project pages are untouched apart from small hooks: boxplots/app.py leaves the page
config to this file, links to Peilbuizen and Refresh data and draws the Pastas return levels, the review page is
read-only inside a Databricks App (data_validation/review_app/common.py: read_only), and
the Overview page leaves its refresh button to Refresh data.
"""

import sys
from pathlib import Path

import streamlit as st

ROOT = Path(__file__).resolve().parent
BOXPLOTS = ROOT / "boxplots"
DATA_VALIDATION = ROOT / "data_validation"

# The projects import their own top-level packages (`modules`, `review_app`,
# `local_validation`); their folders go on the path so those imports keep working.
# ROOT holds `platform_app`; Streamlit adds it too, but tests and notebooks may not.
# pastas/src holds `tarso_pipeline`, whose table names and result readers the Pastas pages share.
for project in (ROOT, BOXPLOTS, DATA_VALIDATION, ROOT / "pastas" / "src"):
    if str(project) not in sys.path:
        sys.path.insert(0, str(project))

from platform_app import nav  # noqa: E402

st.set_page_config(page_title="Grondwater platform", page_icon=":material/water_drop:", layout="wide")
st.session_state["_centrale_app"] = True

st.navigation(nav.SECTIES).run()
