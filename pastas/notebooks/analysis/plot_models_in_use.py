# Databricks notebook source
# MAGIC %md
# MAGIC # Figures for the models in use
# MAGIC
# MAGIC **Run by hand after a model pick in the app** (*Pastas -> Modellen -> Modelvergelijking en
# MAGIC keuze*, pulled into this Git folder). `run_extremes` draws the figures only for the model each
# MAGIC series uses at that moment (`make_plots=in_use`); a model picked later has none yet. This
# MAGIC draws the missing ones from what the run folder already holds (the `.pas` file and the pooled
# MAGIC extremes; nothing is simulated again), and with `prune=true` removes the figures of models
# MAGIC that are no longer in use. `.pas` files and CSVs are never removed.
# MAGIC
# MAGIC Then run `export_results.py` (`plot_models=in_use`) to bring the figures to the app.

# COMMAND ----------

dbutils.widgets.text("source_root", "", "Source root (blank = find pyproject.toml)")
dbutils.widgets.dropdown("data_basis", "validated", ["unvalidated", "validated"], "Train on")
dbutils.widgets.text("volume_base",
                     "/Volumes/waterkeringbeheer_prd/misc_files/misc_files/pastas_pipeline",
                     "Volume prefix")
dbutils.widgets.text("run_folder", "", "Run folder (blank = run_yy_mm_dd today)")
dbutils.widgets.text("exclude_first", "450", "Least-extreme values trimmed per tail (as run_extremes)")
dbutils.widgets.dropdown("only_missing", "true", ["true", "false"], "Only models without figures?")
dbutils.widgets.dropdown("prune", "false", ["true", "false"], "Remove figures of models not in use?")
dbutils.widgets.text("output_catalog", "waterkeringbeheer_prd", "Output catalog")
dbutils.widgets.text("output_schema", "jvanruitenbeek", "Output schema")

# COMMAND ----------

import os
import sys
from pathlib import Path


def _resolve_source_root(given: str) -> str:
    if given:
        root = given.rstrip("/")
        return root if root.endswith("/src") else f"{root}/src"
    current = os.getcwd()
    while True:
        if os.path.isfile(os.path.join(current, "pyproject.toml")):
            return os.path.join(current, "src")
        parent = os.path.dirname(current)
        if parent == current:
            raise RuntimeError(f"No pyproject.toml above {os.getcwd()} and source_root was not set.")
        current = parent


SRC = _resolve_source_root(dbutils.widgets.get("source_root").strip())
if SRC not in sys.path:
    sys.path.insert(0, SRC)

import matplotlib  # noqa: E402

matplotlib.use("Agg")
import pandas as pd  # noqa: E402

from tarso_pipeline.model_plots import plot_models, prune_plots, stems_in_use  # noqa: E402
from tarso_pipeline.paths import basis_volume, output_table, run_dir  # noqa: E402
from tarso_pipeline.results import latest_run_sql  # noqa: E402
from tarso_pipeline.selection import model_choice_path, model_in_use, read_model_choice  # noqa: E402

BASIS = dbutils.widgets.get("data_basis")
VOLUME_BASE = dbutils.widgets.get("volume_base").strip().rstrip("/")
RUN_DIR = run_dir(basis_volume(VOLUME_BASE, BASIS), name=dbutils.widgets.get("run_folder").strip() or None)
EXCLUDE_FIRST = int(dbutils.widgets.get("exclude_first"))
ONLY_MISSING = dbutils.widgets.get("only_missing") == "true"
PRUNE = dbutils.widgets.get("prune") == "true"
CATALOG = dbutils.widgets.get("output_catalog").strip()
SCHEMA = dbutils.widgets.get("output_schema").strip()
METRICS_TABLE = output_table(BASIS, "fit_metrics", CATALOG, SCHEMA)
SELECTION_TABLE = output_table(BASIS, "model_selection", CATALOG, SCHEMA)
print(f"run folder {RUN_DIR}")

# COMMAND ----------

fits = spark.sql(f"SELECT meetreeks_id, series_name, model_type, success FROM ({latest_run_sql(METRICS_TABLE)})"
                 ).toPandas()
fits = fits[fits["success"].astype(bool)]
selection = (spark.sql(latest_run_sql(SELECTION_TABLE, keys=("meetreeks_id",))).toPandas()
             if spark.catalog.tableExists(SELECTION_TABLE) else pd.DataFrame())
in_use = model_in_use(selection, read_model_choice(model_choice_path(Path(SRC).parent, BASIS)),
                      series=fits["meetreeks_id"])
models = in_use.merge(fits[["meetreeks_id", "series_name", "model_type"]].drop_duplicates(),
                      on=["meetreeks_id", "model_type"])
print(f"{len(models)} model(s) in use: {models['model_type'].value_counts().to_dict()}, "
      f"from {models['bron'].value_counts().to_dict()}")

drawn = plot_models(RUN_DIR, models, only_missing=ONLY_MISSING, exclude_first=EXCLUDE_FIRST)
print(drawn["status"].value_counts().to_dict())
display(drawn)

# COMMAND ----------

if PRUNE:
    removed = prune_plots(RUN_DIR, stems_in_use(in_use, fits[["meetreeks_id", "series_name"]]))
    print(f"removed {len(removed)} figure(s) of models not in use from {RUN_DIR}")
else:
    removable = prune_plots(RUN_DIR, stems_in_use(in_use, fits[["meetreeks_id", "series_name"]]), dry_run=True)
    print(f"prune=false; {len(removable)} figure(s) of models not in use could be removed")
