"""The page objects, defined once so pages can `st.switch_page` to each other.

`PAGES` is what the central app (`../app.py`) shows under "Data validation";
`navigation()` is the standalone app (`streamlit_app.py`), where the first page, Map,
is the landing page.
"""

from __future__ import annotations

import streamlit as st

from . import map_page, overview_page, review_page, sources_page

MAP = st.Page(map_page.render, title="Map", icon=":material/map:", url_path="validation-map")
REVIEW = st.Page(review_page.render, title="Review", icon=":material/fact_check:",
                 url_path="validation-review")
OVERVIEW = st.Page(overview_page.render, title="Overview", icon=":material/table_chart:",
                   url_path="validation-overview")

SOURCES = st.Page(sources_page.render, title="Data sources", icon=":material/merge_type:",
                  url_path="validation-sources")

PAGES = [MAP, REVIEW, OVERVIEW, SOURCES]


def navigation():
    return st.navigation(PAGES)
