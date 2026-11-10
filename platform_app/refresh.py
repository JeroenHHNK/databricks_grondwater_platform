"""
Refresh data: the one place that says where each section's data comes from, reloads it,
and checks whether the app may read live data (and write validation results).

The project pages have no refresh buttons of their own in the central app.
"""

from __future__ import annotations

import os
from pathlib import Path

import pandas as pd
import streamlit as st

from modules import databricks_live, databricks_reeksen

from . import bron as bron_module
from . import toegang

ROOT = Path(__file__).resolve().parents[1]
VALIDATIE_EXPORT = ROOT / "data_validation" / "input_output" / "output_databricks"
VALIDATIE_REVIEW = ROOT / "data_validation" / "input_output" / "output_repository"
TOKEN_HEADER = "X-Forwarded-Access-Token"


def _bronnen() -> pd.DataFrame:
    rijen = []
    try:
        b = bron_module.bron()
        heads = ("live from the SQL warehouse" if b.soort == databricks_reeksen.LIVE
                 else "export in boxplots/input_data/databricks_export/")
        heads += f", up to {b.laatste_meting()}"
        if b.fout:
            heads += f" (live failed: {b.fout})"
    except Exception as fout:  # noqa: BLE001
        heads = f"not available: {fout}"
    rijen.append({"Section": "Boxplots, Stijghoogtes", "Data now": heads,
                  "Refreshed by": "live: every new session · export: the boxplots export notebook + redeploy"})

    metadata = VALIDATIE_EXPORT / "metadata" / "object_data.csv"
    reeksen = sum(1 for _ in metadata.open(encoding="utf-8")) - 1 if metadata.is_file() else 0
    reviews = len(list(VALIDATIE_REVIEW.rglob("*.csv"))) if VALIDATIE_REVIEW.is_dir() else 0
    rijen.append({"Section": "Data validation",
                  "Data now": f"{reeksen} series in the repo export · {reviews} reviewed series",
                  "Refreshed by": "the groundwater_source_replication job + git, then a redeploy"})
    return pd.DataFrame(rijen)


def _herlaad() -> None:
    st.cache_data.clear()
    st.cache_resource.clear()
    bron_module.vergeet()


def _uitleg() -> None:
    st.subheader("How to refresh the source data")
    st.markdown(
        "The deployed app runs on a **copy of the repo** taken at deploy time. New files only "
        "reach it after they are pushed to `main` and the app is **deployed again** "
        "(Compute → Apps → the app → Deploy). Live heads are the exception: they are read from "
        "the warehouse in every new session."
    )
    with st.expander("Boxplots and Stijghoogtes: groundwater heads", expanded=True):
        st.markdown(
            "**Live (preferred):** set `DATABRICKS_WAREHOUSE_ID` in `app.yaml`, add the `sql-warehouse` "
            "resource to the app, and give the app the read grants (check below). Then every new session "
            "has today's heads and *Reload in this app* fetches them again.\n\n"
            "**Export (until then):**\n"
            "1. In the Databricks Git folder, pull `main`.\n"
            "2. Run `boxplots/notebooks/db_notebook_exporteer_stijghoogtes.py` "
            "(and `db_notebook_exporteer_meetreeksen.py` when wells were added).\n"
            "3. Commit and push `boxplots/input_data/databricks_export/` from the Git folder.\n"
            "4. Deploy the app again."
        )
    with st.expander("Data validation: source copy and review decisions"):
        st.markdown(
            "1. Run the job `groundwater_source_replication` "
            "(`data_validation/databricks_code/jobs/workflow_config.json`: 00 → 01 → 02). "
            "It writes `data_validation/input_output/output_databricks/`; with `transport=write`, commit and push "
            "that folder from the Git folder.\n"
            "2. Locally: `git pull`, run the checks (`local_validation/validate.ipynb`), review in this app "
            "(saving works locally), then commit and push `input_output/output_repository/`.\n"
            "3. In Databricks: pull, run `03_validated_to_delta` → "
            f"`{toegang.doel_schema()}.{toegang.VALIDATIE_TABEL}`.\n"
            "4. Deploy the app again to show the new state here."
        )
    with st.expander("Pastas: model fits and extremes (not shown in the app)"):
        st.markdown(
            "Run the job or notebooks in `pastas/notebooks/` (ingestion → transformation). They write Delta "
            "tables in the personal schema and files in the `misc_files` volume; see "
            "`pastas/docs/DATABRICKS_PERMISSIONS.md`."
        )


def _toegang() -> None:
    st.subheader("Live data access")
    if not databricks_live.beschikbaar():
        st.info(
            f"`{databricks_live.WAREHOUSE_VAR}` is not set, so the pages use the export. To switch live on in the "
            "deployed app: add the SQL warehouse as an app resource with key `sql-warehouse` (that gives the app "
            "*Can use*), uncomment the two lines in `app.yaml` and deploy again. You can check the grants first "
            "by entering the warehouse ID below (SQL Warehouses → the warehouse → Connection details)."
        )
    warehouse = st.text_input("SQL warehouse ID", os.environ.get(databricks_live.WAREHOUSE_VAR, "").strip())
    if not warehouse:
        return

    token = st.context.headers.get(TOKEN_HEADER)
    identiteiten = ["The app (its service principal)"] + (["You (user authorization)"] if token else [])
    wie = st.radio("Check as", identiteiten, horizontal=True,
                   help="The app reads as its service principal. With user authorization switched on for the "
                        "app, it can also query as the person viewing it, with that person's own grants.")
    schema = st.text_input("Schema for validation results", toegang.doel_schema(),
                           help=f"Default from `{toegang.SCHEMA_VAR}`, otherwise the personal schema the "
                                "data validation notebooks write to.")
    schrijf = st.checkbox(f"Also test writing (creates and drops `{toegang.SCHRIJFTEST_TABEL}` in that schema)")
    if not st.button("Check access", type="primary"):
        return
    try:
        with st.spinner("Checking…"), databricks_live.verbinding(
                token if wie.startswith("You") else None, warehouse) as cursor:
            ik, uitkomsten = toegang.controleer(cursor, schema, schrijftest=schrijf)
    except Exception as fout:  # noqa: BLE001
        st.error(f"Could not connect to the SQL warehouse: {fout}")
        st.markdown("Check that the app has the `sql-warehouse` resource with *Can use*, and that the warehouse "
                    "is running or can start.")
        return

    st.dataframe(pd.DataFrame([{"": "✅" if u.gelukt else "❌", "Check": u.controle, "Result": u.detail}
                               for u in uitkomsten]), hide_index=True, width="stretch")
    oplossingen = list(dict.fromkeys(u.oplossing for u in uitkomsten if not u.gelukt and u.oplossing))
    if oplossingen:
        st.warning(f"Missing grants for `{ik}`. An admin (or the schema owner) can run:")
        st.code("\n".join(oplossingen), language="sql")
    else:
        st.success(f"`{ik}` has everything checked here.")


def render() -> None:
    st.title("Refresh data")
    st.caption("One place for where the data comes from, how to refresh it, and whether live access works.")
    # The button first, so the table below already shows the reloaded data.
    if st.button("↻ Reload in this app", help="Clears the app's caches. Live heads are fetched again; "
                                                "files are read again from this deployment."):
        _herlaad()
        st.success("Reloaded. Pages read their data again when you open them.")
    st.table(_bronnen().set_index("Section"))
    _uitleg()
    _toegang()
