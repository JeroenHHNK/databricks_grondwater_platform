"""
The groundwater heads of this session, shared by Boxplots and Stijghoogtes.

Same session keys as boxplots/app.py (`bron`, `opgehaald`), so the heads are
fetched once per session whichever page opens first, and Refresh data has one
key to clear.
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import streamlit as st

from modules import databricks_reeksen, paths

SESSIE_BRON = "bron"
SESSIE_OPGEHAALD = "opgehaald"


def bron() -> databricks_reeksen.Bron:
    """Live if a warehouse is set (and works), otherwise the export; see databricks_reeksen.bron()."""
    if SESSIE_BRON not in st.session_state:
        with st.spinner("Stijghoogtes ophalen…"):
            st.session_state[SESSIE_BRON] = databricks_reeksen.bron()
        st.session_state[SESSIE_OPGEHAALD] = datetime.now(ZoneInfo("Europe/Amsterdam"))
    return st.session_state[SESSIE_BRON]


def vergeet() -> None:
    """Fetch again on the next page that needs the heads."""
    st.session_state.pop(SESSIE_BRON, None)
    st.session_state.pop(SESSIE_OPGEHAALD, None)


def versie(b: databricks_reeksen.Bron) -> tuple[float, str]:
    """Cache key: config.csv's mtime and what the heads are (fetch time live, file mtime for the export)."""
    if b.soort == databricks_reeksen.LIVE:
        wat = st.session_state[SESSIE_OPGEHAALD].isoformat()
    else:
        wat = f"export {(databricks_reeksen.export_map() / databricks_reeksen.DAG_CSV).stat().st_mtime}"
    return paths.config_csv().stat().st_mtime, wat


def herkomst(b: databricks_reeksen.Bron) -> str:
    """Where the heads come from, for a caption."""
    if b.soort == databricks_reeksen.LIVE:
        return (f"live uit Databricks t/m {b.laatste_meting()} "
                f"(opgehaald {st.session_state[SESSIE_OPGEHAALD]:%H:%M})")
    return f"uit de export t/m {b.laatste_meting()}"
