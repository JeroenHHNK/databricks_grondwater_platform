# Databricks notebook source
# MAGIC %md
# MAGIC # Fit Pastas models for the manifest series
# MAGIC
# MAGIC Task 2 of the pipeline. Reads the manifest, fits one model per series, writes each
# MAGIC `.pas` to the models volume, and appends one row per fit to
# MAGIC `waterkeringbeheer_prd.jvanruitenbeek.pastas_fit_metrics`.
# MAGIC
# MAGIC ### Run it on one series first
# MAGIC
# MAGIC Set `limit_series` to 1. Build order step 3 is "fit **one** series end to end"
# MAGIC before scaling to 20 — the volume write in particular is an untested grant.
# MAGIC
# MAGIC ### Metrics come from the API, not `fit_report()`
# MAGIC
# MAGIC `ml.fit_report()` is a human-formatted string whose layout is not a stable API.
# MAGIC Every value here is read through `ml.stats.*`, `ml.solver.*`, `ml.settings` and
# MAGIC `ml.parameters`, each guarded individually so a field a given Pastas release does
# MAGIC not expose lands as `NULL` instead of losing the row.
# MAGIC
# MAGIC ### Failures are rows
# MAGIC
# MAGIC `fit_series` never raises. A series that fails gets `success=false` and the
# MAGIC message, so one malformed series cannot abort a run over twenty.
# MAGIC
# MAGIC ### Forcing
# MAGIC
# MAGIC Open question 2 from the handover is already answered: the stress series ship
# MAGIC with this repo at `data/forcing/knmi_forcing_249.csv` (KNMI 249 Berkhout, 2020
# MAGIC onwards), because the cluster has no outbound internet. Refresh with
# MAGIC `python scripts/check_knmi.py` locally, then commit, push and pull.

# COMMAND ----------

dbutils.widgets.text("source_table", "gold_prd.hl_grondwaterpeilmeting.meting", "Source")
dbutils.widgets.text("manifest_table",
                     "waterkeringbeheer_prd.jvanruitenbeek.pastas_series_manifest",
                     "Manifest table")
dbutils.widgets.text("metrics_table",
                     "waterkeringbeheer_prd.jvanruitenbeek.pastas_fit_metrics",
                     "Metrics table")
dbutils.widgets.text("model_volume",
                     "/Volumes/waterkeringbeheer_prd/misc_files/misc_files/pastas_pipeline",
                     "Models volume")

dbutils.widgets.multiselect("model_types", "tarso", ["tarso", "recharge"], "Model types")
dbutils.widgets.text("limit_series", "1", "How many series (start with 1)")
dbutils.widgets.text("station", "249", "KNMI station (forcing)")

dbutils.widgets.text("dedup_method", "mean", "Collapse duplicate timestamps with")
dbutils.widgets.text("resample_rule", "D", "Resample rule")
dbutils.widgets.text("resample_method", "mean", "Resample method")
dbutils.widgets.text("min_obs", "100", "Minimum observations after cleaning")
dbutils.widgets.text("min_days", "365", "Minimum span after cleaning")
dbutils.widgets.text("run_id", "", "Run id (blank = timestamp)")
dbutils.widgets.dropdown("write_metrics", "true", ["true", "false"], "Write metrics?")

# COMMAND ----------

# MAGIC %pip install "pastas==1.12.*"
# MAGIC %restart_python

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
            raise RuntimeError(
                f"No pyproject.toml above {os.getcwd()}. Is this notebook running "
                "inside the Git folder for this repo?"
            )
        current = parent


SRC = os.path.join(_repo_root(), "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

import matplotlib

matplotlib.use("Agg")

import pandas as pd
import pastas as ps
from pyspark.sql import functions as F

from tarso_pipeline.fit import fit_series
from tarso_pipeline.fit_metrics import rows_to_frame, spark_schema
from tarso_pipeline.io import load_forcing
from tarso_pipeline.meting import series_rows_sql
from tarso_pipeline.paths import forcing_path, model_dir_for
from tarso_pipeline.train import make_run_id

SOURCE = dbutils.widgets.get("source_table").strip()
MANIFEST = dbutils.widgets.get("manifest_table").strip()
METRICS = dbutils.widgets.get("metrics_table").strip()
MODEL_VOLUME = dbutils.widgets.get("model_volume").strip() or None
MODEL_TYPES = [m for m in dbutils.widgets.get("model_types").split(",") if m.strip()]
LIMIT_SERIES = int(dbutils.widgets.get("limit_series"))
STATION = int(dbutils.widgets.get("station"))
DEDUP_METHOD = dbutils.widgets.get("dedup_method")
RESAMPLE_RULE = dbutils.widgets.get("resample_rule")
RESAMPLE_METHOD = dbutils.widgets.get("resample_method")
MIN_OBS = int(dbutils.widgets.get("min_obs"))
MIN_DAYS = int(dbutils.widgets.get("min_days"))
RUN_ID = dbutils.widgets.get("run_id").strip() or make_run_id()
WRITE_METRICS = dbutils.widgets.get("write_metrics") == "true"

print(f"run_id       {RUN_ID}")
print(f"manifest     {MANIFEST}")
print(f"metrics      {METRICS}")
print(f"models       {MODEL_VOLUME or '(not written)'}")
print(f"model types  {MODEL_TYPES}")
print(f"series       {LIMIT_SERIES}")
print(f"cleaning     dedup={DEDUP_METHOD}, resample={RESAMPLE_RULE}/{RESAMPLE_METHOD}")
print(f"pastas       {ps.__version__}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Confirm the volume is writable
# MAGIC
# MAGIC `WRITE VOLUME` on `misc_files` is a **separate grant** from the table write that
# MAGIC was tested. Better to fail here on a throwaway file than twenty fits in.

# COMMAND ----------

if MODEL_VOLUME:
    probe = os.path.join(MODEL_VOLUME, "__write_test.txt")
    try:
        os.makedirs(MODEL_VOLUME, exist_ok=True)
        with open(probe, "w") as handle:
            handle.write("ok")
        os.remove(probe)
        print(f"writable: {MODEL_VOLUME}")
    except Exception as exc:
        raise RuntimeError(
            f"Cannot write to {MODEL_VOLUME}: {type(exc).__name__}: {exc}\n"
            "Either request WRITE VOLUME on misc_files, or blank the model_volume "
            "widget to run without saving .pas files."
        ) from exc
else:
    print("model_volume blank; .pas files will not be written")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Forcing

# COMMAND ----------

FORCING_FILE = str(forcing_path(STATION))
precipitation = load_forcing(FORCING_FILE, "precipitation_mm")
evaporation = load_forcing(FORCING_FILE, "makkink_mm")
print(f"{FORCING_FILE}")
print(f"{len(precipitation)} days, {precipitation.index.min().date()} -> "
      f"{precipitation.index.max().date()}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## The work list

# COMMAND ----------

work = (
    spark.table(MANIFEST)
    .orderBy("span_days", ascending=False)
    .limit(LIMIT_SERIES)
    .toPandas()
)
print(f"{len(work)} series to fit x {len(MODEL_TYPES)} model type(s) "
      f"= {len(work) * len(MODEL_TYPES)} fits")
display(work)

# COMMAND ----------

# MAGIC %md
# MAGIC ## Fit
# MAGIC
# MAGIC Series are loaded one at a time on the driver: each is a few tens of thousands of
# MAGIC rows, and the forcing is shared. Scale out with a Job "For each" task over
# MAGIC `series_id` once one series is known good — that gives per-series retries without
# MAGIC restructuring any of this.

# COMMAND ----------

rows = []
for item in work.itertuples():
    raw = spark.sql(series_rows_sql(SOURCE, item.series_id)).toPandas()
    # The hash is the only name available (handover open question 3); shorten it
    # for folder and log readability while keeping the full id on the row.
    series_name = f"series_{item.series_id[:16]}"
    print(f"\n{series_name}  ({len(raw):,} raw rows, span {item.span_days}d)")

    for model_type in MODEL_TYPES:
        row = fit_series(
            series_id=item.series_id,
            series_name=series_name,
            raw=raw,
            precipitation=precipitation,
            evaporation=evaporation,
            run_id=RUN_ID,
            model_type=model_type,
            model_dir=model_dir_for(series_name, MODEL_VOLUME) if MODEL_VOLUME else None,
            dedup_method=DEDUP_METHOD,
            resample_rule=RESAMPLE_RULE,
            resample_method=RESAMPLE_METHOD,
            min_obs=MIN_OBS,
            min_days=MIN_DAYS,
        )
        rows.append(row)
        if row["success"]:
            print(f"   {model_type:9} OK   EVP={row['evp']:.1f}%  R2={row['r2']:.3f}  "
                  f"RMSE={row['rmse']:.3f}  nobs={row['nobs']}  "
                  f"(dedup removed {row['n_duplicate_timestamps']:,})")
        else:
            print(f"   {model_type:9} FAIL {row['error_message']}")

results = rows_to_frame(rows)
print(f"\n{int(results['success'].sum())} of {len(results)} fits succeeded")

# COMMAND ----------

display(
    results[["series_name", "model_type", "success", "nobs", "span_days",
             "evp", "r2", "rmse", "aicc", "bic", "obj",
             "n_raw_rows", "n_duplicate_timestamps", "model_path", "error_message"]]
)

# COMMAND ----------

# MAGIC %md
# MAGIC ## Persist
# MAGIC
# MAGIC Append with a distinct `run_id`, so history accumulates and runs stay comparable.

# COMMAND ----------

if not WRITE_METRICS:
    print("write_metrics=false; nothing written")
elif results.empty:
    print("no rows to write")
else:
    spark.createDataFrame(results, schema=spark_schema()).write.format("delta").mode(
        "append"
    ).option("mergeSchema", "true").saveAsTable(METRICS)
    print(f"appended {len(results)} rows to {METRICS} as run {RUN_ID}")

    display(
        spark.table(METRICS)
        .select("run_id", "series_name", "model_type", "success", "evp", "r2", "rmse")
        .orderBy(F.col("fitted_at").desc())
        .limit(20)
    )
