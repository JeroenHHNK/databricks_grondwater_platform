# Databricks notebook source
# /// script
# [tool.databricks.environment]
# environment_version = "6"
# ///
# MAGIC %md
# MAGIC # Shape and uncertainty of the extremes (optional)
# MAGIC
# MAGIC **Run after `transformation/run_extremes`, with the same `data_basis` and `run_folder`.**
# MAGIC Nothing downstream needs it: `export_results` and the app run without it. It is the
# MAGIC slow, bootstrap half that used to sit inside `run_extremes`, split off so the main
# MAGIC chain stays fast:
# MAGIC
# MAGIC ```
# MAGIC 03_validated_to_delta -> build_manifest -> fit_pastas_models -> run_extremes -> export_results
# MAGIC                                                                     \-> explore_shape_and_uncertainty
# MAGIC ```
# MAGIC
# MAGIC It reads `pooled_extremes/<series>_{dry,wet}.csv` from the run folder (nothing is
# MAGIC simulated again) and bootstraps the GPD and the exponential above the threshold
# MAGIC `replicates` times per tail. That gives:
# MAGIC
# MAGIC 1. **The bootstrap SE of the GPD shape**, next to the asymptotic one the threshold plot
# MAGIC    draws.
# MAGIC 2. **The 95% band of the GPD and exponential return levels** per return period, and the
# MAGIC    `confidence_plots/<series>_return_level.png` figure.
# MAGIC
# MAGIC Neither band contains the uncertainty of the Tarso model itself (its parameters are taken
# MAGIC as exact), which is probably the larger part.
# MAGIC
# MAGIC Writes, per data basis, `<prefix>_shape_uncertainty` (one row per model x tail) and
# MAGIC `<prefix>_return_level_ci` (one row per model x tail x T), appended with the `run_id`
# MAGIC of the extremes run they belong to, plus `shape_uncertainty.csv` and
# MAGIC `return_level_ci.csv` in the run folder.
# MAGIC
# MAGIC It installs the same pastas pin as `run_extremes`, so scipy, and with it every fit,
# MAGIC is the same version. Cost: a few seconds per model at 500 replicates. Use
# MAGIC `limit_models` to try it on a few first.

# COMMAND ----------

dbutils.widgets.text("source_root", "", "Source root (blank = find pyproject.toml)")
dbutils.widgets.dropdown("data_basis", "unvalidated", ["unvalidated", "validated"], "Results of")
dbutils.widgets.text("volume_base",
                     "/Volumes/waterkeringbeheer_prd/misc_files/misc_files/pastas_pipeline",
                     "Volume prefix")
dbutils.widgets.text("run_folder", "", "Run folder (blank = run_yy_mm_dd today)")
dbutils.widgets.text("exclude_first", "",
                     "Least-extreme values trimmed (blank = as the extremes run)")
dbutils.widgets.text("t_list", "2 5 10 25 50 100 250 500 1000", "Return periods")
dbutils.widgets.text("replicates", "500", "Bootstrap replicates per tail")
dbutils.widgets.text("seed", "0", "Bootstrap seed")
dbutils.widgets.text("limit_models", "0", "Max models (0 = all)")
dbutils.widgets.dropdown("make_plots", "true", ["true", "false"], "Make confidence plots?")
dbutils.widgets.dropdown("write_tables", "true", ["true", "false"], "Write Delta tables?")
dbutils.widgets.text("output_catalog", "waterkeringbeheer_prd", "Output catalog")
dbutils.widgets.text("output_schema", "jvanruitenbeek", "Output schema")

# COMMAND ----------

# MAGIC %pip install "pastas==1.12.*"
# MAGIC %restart_python

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
            raise RuntimeError(
                f"No pyproject.toml above {os.getcwd()} and source_root was not set."
            )
        current = parent


SRC = _resolve_source_root(dbutils.widgets.get("source_root").strip())
if SRC not in sys.path:
    sys.path.insert(0, SRC)

import matplotlib

matplotlib.use("Agg")

import pandas as pd
from pyspark.sql import functions as F

from tarso_pipeline.evt_plots import confidence_plot
from tarso_pipeline.evt_report import tail_tables
from tarso_pipeline.paths import (
    CONFIDENCE_PLOTS_DIR,
    EVT_SELECTION_FILE,
    POOLED_EXTREMES_DIR,
    basis_volume,
    extremes_dir,
    extremes_summary_file,
    output_table,
    run_dir,
)
from tarso_pipeline.uncertainty import (
    RETURN_LEVEL_CI_COLUMNS,
    SHAPE_UNCERTAINTY_COLUMNS,
    model_uncertainty,
    pooled_series,
    read_pooled,
)

BASIS = dbutils.widgets.get("data_basis")
VOLUME_BASE = dbutils.widgets.get("volume_base").strip().rstrip("/")
RUN_DIR = run_dir(basis_volume(VOLUME_BASE, BASIS),
                  name=dbutils.widgets.get("run_folder").strip() or None)
T_LIST = tuple(int(t) for t in dbutils.widgets.get("t_list").split())
REPLICATES = int(dbutils.widgets.get("replicates"))
SEED = int(dbutils.widgets.get("seed"))
LIMIT_MODELS = int(dbutils.widgets.get("limit_models"))
MAKE_PLOTS = dbutils.widgets.get("make_plots") == "true"
WRITE_TABLES = dbutils.widgets.get("write_tables") == "true"
CATALOG = dbutils.widgets.get("output_catalog").strip()
SCHEMA = dbutils.widgets.get("output_schema").strip()

SHAPE_TABLE = output_table(BASIS, "shape_uncertainty", CATALOG, SCHEMA)
CI_TABLE = output_table(BASIS, "return_level_ci", CATALOG, SCHEMA)
POOLED_DIR = extremes_dir(RUN_DIR, POOLED_EXTREMES_DIR)

if not os.path.isdir(POOLED_DIR):
    raise NotADirectoryError(
        f"No {POOLED_DIR}. Has run_extremes run for this data_basis and run_folder?"
    )

# COMMAND ----------

# MAGIC %md
# MAGIC ## What the extremes run did
# MAGIC
# MAGIC Its `evt_selection.csv` gives the full `meetreeks_id`, its `run_id` (so the rows here
# MAGIC join to `<prefix>_evt_selection` and `<prefix>_return_levels`).
# MAGIC Its summary gives the `exclude_first` it trimmed with, which is used unless set above.

# COMMAND ----------

selection_path = os.path.join(RUN_DIR, EVT_SELECTION_FILE)
selection = pd.read_csv(selection_path) if os.path.isfile(selection_path) else pd.DataFrame()
summary_path = extremes_summary_file(RUN_DIR)
summary = pd.read_csv(summary_path) if os.path.isfile(summary_path) else pd.DataFrame()

given_trim = dbutils.widgets.get("exclude_first").strip()
if given_trim:
    EXCLUDE_FIRST = int(given_trim)
elif "exclude_first" in summary.columns and summary["exclude_first"].notna().any():
    trims = summary["exclude_first"].dropna().astype(int).unique()
    if len(trims) > 1:
        raise ValueError(f"The extremes summary mixes exclude_first values {sorted(trims)}; "
                         "set exclude_first explicitly.")
    EXCLUDE_FIRST = int(trims[0])
else:
    raise ValueError(f"No exclude_first in {summary_path}; set the exclude_first widget.")

models = pd.DataFrame({"series_name": pooled_series(POOLED_DIR)})
if not selection.empty:
    ids = (selection[["series_name", "meetreeks_id", "run_id"]]
           .drop_duplicates("series_name", keep="last"))
    models = models.merge(ids, on="series_name", how="left")
else:
    models["meetreeks_id"] = None
    models["run_id"] = None
models["meetreeks_id"] = models["meetreeks_id"].fillna(models["series_name"])
models["run_id"] = models["run_id"].fillna(os.path.basename(RUN_DIR.rstrip("/")))
if LIMIT_MODELS:
    models = models.head(LIMIT_MODELS)

print(f"basis         {BASIS}")
print(f"run folder    {RUN_DIR}")
print(f"trim          {EXCLUDE_FIRST}   replicates {REPLICATES}   seed {SEED}")
print(f"T list        {T_LIST}")
print(f"{len(models)} model(s) with pooled extremes")
display(models)

# COMMAND ----------

# MAGIC %md
# MAGIC ## Bootstrap per model

# COMMAND ----------

shape_rows, level_rows, plot_paths = [], [], {}
os.makedirs(extremes_dir(RUN_DIR, CONFIDENCE_PLOTS_DIR), exist_ok=True)

for item in models.itertuples():
    pooled = read_pooled(POOLED_DIR, item.series_name)
    if not pooled:
        print(f"{item.series_name}: no pooled extremes, skipped")
        continue
    shapes, levels, per_tail = model_uncertainty(
        pooled, item.meetreeks_id, item.series_name, item.run_id,
        exclude_first=EXCLUDE_FIRST, t_list=T_LIST, replicates=REPLICATES, seed=SEED,
    )
    shape_rows.extend(shapes)
    level_rows.extend(levels)

    for row in shapes:
        print(f"{item.series_name}  {row['tail']:4} GPD shape={row['gpd_shape']:+.3f}  "
              f"SE asymptotic={row['gpd_shape_se_asymptotic']:.3f} bootstrap={row['gpd_shape_se_bootstrap']:.3f}")

    if MAKE_PLOTS and per_tail:
        # The figure needs the fit and the empirical points too; refitting those is cheap.
        figure_tails = {
            tail: tail_tables(pooled[tail], tail, exclude_first=EXCLUDE_FIRST, t_list=T_LIST)
            for tail in per_tail
        }
        plot_paths[item.series_name] = confidence_plot(
            figure_tails,
            Path(extremes_dir(RUN_DIR, CONFIDENCE_PLOTS_DIR))
            / f"{item.series_name}_return_level.png",
            f"Return level with 95% CI - {item.series_name}",
            resamples={tail: result["resamples"] for tail, result in per_tail.items()},
        )

shape_frame = pd.DataFrame(shape_rows, columns=SHAPE_UNCERTAINTY_COLUMNS)
ci_frame = pd.DataFrame(level_rows, columns=RETURN_LEVEL_CI_COLUMNS)
print(f"\n{shape_frame['series_name'].nunique()} model(s), {len(shape_frame)} tail(s)")

# COMMAND ----------

# MAGIC %md
# MAGIC ## The GPD shape and how sure it is
# MAGIC
# MAGIC Below 0 the tail flattens off (GPD), around 0 it does not (exponential). The two SEs
# MAGIC should be close; a shape within about 2 SE of 0 cannot tell the two apart.

# COMMAND ----------

display(shape_frame[["series_name", "tail", "n_kept", "gpd_shape", "gpd_shape_se_asymptotic",
                     "gpd_shape_se_bootstrap"]])

# COMMAND ----------

# MAGIC %md
# MAGIC ## The width of the answer
# MAGIC
# MAGIC The 95% band of the GPD and exponential levels, in metres. T=1000 is extrapolation
# MAGIC whatever the band says.

# COMMAND ----------

if not ci_frame.empty:
    display(ci_frame.pivot_table(index=["series_name", "tail", "family"], columns="return_period",
                                 values="ci_width").round(3))

# COMMAND ----------

# MAGIC %md
# MAGIC ## Write

# COMMAND ----------

shape_frame.to_csv(os.path.join(RUN_DIR, "shape_uncertainty.csv"), index=False)
ci_frame.to_csv(os.path.join(RUN_DIR, "return_level_ci.csv"), index=False)
print(f"wrote shape_uncertainty.csv and return_level_ci.csv in {RUN_DIR}")
print(f"{sum(p is not None for p in plot_paths.values())} confidence plot(s) in "
      f"{extremes_dir(RUN_DIR, CONFIDENCE_PLOTS_DIR)}")

if not WRITE_TABLES:
    print("write_tables=false; no Delta tables written")
else:
    spark.sql(f"CREATE SCHEMA IF NOT EXISTS {CATALOG}.{SCHEMA}")
    for frame, table in ((shape_frame, SHAPE_TABLE), (ci_frame, CI_TABLE)):
        if frame.empty:
            print(f"nothing to write to {table}")
            continue
        (
            spark.createDataFrame(frame.astype(object).where(pd.notna(frame), None))
            .withColumn("replicates", F.lit(REPLICATES))
            .withColumn("written_at", F.current_timestamp())
            .write.format("delta").mode("append").option("mergeSchema", "true")
            .saveAsTable(table)
        )
        print(f"appended {len(frame)} rows to {table}")
