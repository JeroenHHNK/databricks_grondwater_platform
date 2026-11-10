# Databricks notebook source
# MAGIC %md
# MAGIC # Export the results for the Streamlit app
# MAGIC
# MAGIC **Run by hand after a fit (and extremes) run.** The app reads the Pastas results live
# MAGIC from the SQL warehouse when it has access (`DATABRICKS_WAREHOUSE_ID` plus `SELECT` on
# MAGIC the `<prefix>_*` tables; see the root README, "Pastas results in the app"). Until then
# MAGIC it reads this export: the latest run per series, written into this Git folder as
# MAGIC
# MAGIC ```
# MAGIC pastas/results_export/<prefix>/
# MAGIC     fit_metrics.csv      model_checks.csv    evt_selection.csv
# MAGIC     return_levels.csv    simulations.parquet (daily observed + simulated head)
# MAGIC ```
# MAGIC
# MAGIC Then commit and push `pastas/results_export/` from the Git folder UI and redeploy the
# MAGIC app (or pull locally). Same route as the Boxplots export.
# MAGIC
# MAGIC `<prefix>` is `unvalid_pastas` or `valid_pastas`, from `data_basis`. Run once per basis.

# COMMAND ----------

dbutils.widgets.dropdown("data_basis", "unvalidated", ["unvalidated", "validated"], "Results of")
dbutils.widgets.text("output_catalog", "waterkeringbeheer_prd", "Output catalog")
dbutils.widgets.text("output_schema", "jvanruitenbeek", "Output schema")
dbutils.widgets.text("model_types", "tarso", "Model types to export (comma-separated)")

# COMMAND ----------

import os
import sys


def _repo_root() -> str:
    current = os.getcwd()
    while True:
        if os.path.isfile(os.path.join(current, "pyproject.toml")):
            return current
        parent = os.path.dirname(current)
        if parent == current:
            raise RuntimeError(f"No pyproject.toml above {os.getcwd()}; run this from the Git folder.")
        current = parent


ROOT = _repo_root()
if os.path.join(ROOT, "src") not in sys.path:
    sys.path.insert(0, os.path.join(ROOT, "src"))

from tarso_pipeline.paths import output_table  # noqa: E402
from tarso_pipeline.results import EXPORT_FILES, export_dir, latest_run_sql  # noqa: E402

BASIS = dbutils.widgets.get("data_basis")
CATALOG = dbutils.widgets.get("output_catalog").strip()
SCHEMA = dbutils.widgets.get("output_schema").strip()
MODEL_TYPES = [m.strip() for m in dbutils.widgets.get("model_types").split(",") if m.strip()]
OUT = export_dir(ROOT, BASIS)
OUT.mkdir(parents=True, exist_ok=True)
print(f"basis {BASIS} -> {OUT}")

# COMMAND ----------

import pandas as pd  # noqa: E402

quoted = ", ".join("'" + m.replace("'", "") + "'" for m in MODEL_TYPES)
for kind, filename in EXPORT_FILES.items():
    table = output_table(BASIS, kind, CATALOG, SCHEMA)
    if not spark.catalog.tableExists(table):
        print(f"skip {kind}: {table} does not exist (has that stage run for this basis?)")
        continue
    frame = spark.sql(f"SELECT * FROM ({latest_run_sql(table)}) WHERE model_type IN ({quoted})").toPandas()
    path = OUT / filename
    if filename.endswith(".parquet"):
        frame.to_parquet(path, index=False)
    else:
        frame.to_csv(path, index=False)
    print(f"{kind:15} {len(frame):>8,} rows, {frame['meetreeks_id'].nunique():>4} series -> {path.name} "
          f"({path.stat().st_size / 1e6:.1f} MB)")

print("\nNow commit and push pastas/results_export/ from the Git folder UI.")
