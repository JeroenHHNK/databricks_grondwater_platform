"""
Central Streamlit app (Databricks App) for the groundwater platform.

Two sections in the navigation pane, one per project:

- Boxplots: cross-sections with soil build-up, wells and heads (boxplots/app.py).
- Data validation: map, point-by-point review and progress (data_validation/review_app/).

Each project keeps its own folder, code and data; this file only puts them side by side.
Run from the repo root:

    uv run streamlit run app.py

The project pages are untouched apart from two hooks: boxplots/app.py leaves the page
config to this file, and the review page is read-only inside a Databricks App
(see data_validation/review_app/common.py: read_only).
"""

import sys
from pathlib import Path

import streamlit as st

ROOT = Path(__file__).resolve().parent
BOXPLOTS = ROOT / "boxplots"
DATA_VALIDATION = ROOT / "data_validation"

# The projects import their own top-level packages (`modules`, `review_app`,
# `local_validation`); their folders go on the path so those imports keep working.
for project in (BOXPLOTS, DATA_VALIDATION):
    if str(project) not in sys.path:
        sys.path.insert(0, str(project))

from review_app import nav as validation_nav  # noqa: E402

st.set_page_config(page_title="Grondwater platform", page_icon=":material/water_drop:", layout="wide")
st.session_state["_centrale_app"] = True

BOXPLOTS_PAGE = st.Page(
    BOXPLOTS / "app.py", title="Dwarsprofielen", icon=":material/stacked_line_chart:",
    url_path="boxplots", default=True,
)

st.navigation({
    "Boxplots": [BOXPLOTS_PAGE],
    "Data validation": validation_nav.PAGES,
}).run()
