# Databricks notebook source
# MAGIC %md
# MAGIC # Train a Tarso model per head series
# MAGIC
# MAGIC Discovers head CSVs under a Unity Catalog Volume, fits one Pastas `TarsoModel`
# MAGIC per series, writes each fitted model as a `.pas` file and appends one row per
# MAGIC series to the `tarso_runs` Delta table.
# MAGIC
# MAGIC ### Which series are trained
# MAGIC
# MAGIC Only those spanning at least **`min_days` (default 365)** calendar days. A Tarso
# MAGIC fit has to see the seasonal cycle; a few months of data produces numbers that
# MAGIC *look* like a result but are not one. Shorter series are **not dropped** — they
# MAGIC get a row with `status="skipped"` and the reason, so nothing disappears silently.
# MAGIC
# MAGIC The separate `min_obs` rule (default 100 observations) still applies, but to
# MAGIC **validated files only**, as the spec defines it.
# MAGIC
# MAGIC ### One failure must not kill the run
# MAGIC
# MAGIC Every fit is wrapped: a series that throws becomes a row with `status="error"`
# MAGIC and the exception message. Hundreds of series are processed and individual fits
# MAGIC genuinely fail — degenerate records, non-converging solvers — so the run has to
# MAGIC survive them.
# MAGIC
# MAGIC ### Forcing
# MAGIC
# MAGIC Read from `data/forcing/`, which ships with the repo because the cluster has no
# MAGIC outbound internet. Refresh it with `python scripts/check_knmi.py` locally, then
# MAGIC commit, push and pull the Git folder.

# COMMAND ----------

# MAGIC %md
# MAGIC ## Parameters

# COMMAND ----------

dbutils.widgets.text("catalog", "dev_catalog", "Catalog")
dbutils.widgets.text("schema", "default", "Schema")
dbutils.widgets.text("output_table", "tarso_runs", "Output table")

dbutils.widgets.text("input_root", "", "Head CSV root (/Volumes/.../input_files)")
dbutils.widgets.text("surveys", "", "Survey folders, comma separated (blank = all)")
dbutils.widgets.text("model_dir", "", "Where .pas files go (/Volumes/.../models)")

dbutils.widgets.text("station", "249", "KNMI station (forcing)")
dbutils.widgets.text("min_days", "365", "Minimum series span in days")
dbutils.widgets.text("min_obs", "100", "Minimum observations (validated only)")
dbutils.widgets.text("head_column", "head", "Preferred raw value column")
dbutils.widgets.text("resample_rule", "D", "Resample rule")
dbutils.widgets.text("resample_method", "mean", "Resample method")
dbutils.widgets.dropdown("validation_filter", "true", ["true", "false"], "Validation filter")
dbutils.widgets.text("tmin_cal", "", "Calibration start (blank = series start)")
dbutils.widgets.text("tmax_cal", "", "Calibration end (blank = series end)")
dbutils.widgets.text("run_id", "", "Run id (blank = yy_mm_dd_HH_MM now)")

# COMMAND ----------

# MAGIC %md
# MAGIC Pastas is not on the cluster by default. This restarts Python, so it must run
# MAGIC before the imports below.

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

matplotlib.use("Agg")  # spec section 5: force Agg if matplotlib is imported at all

import pandas as pd
import pastas as ps
from pyspark.sql import functions as F

from tarso_pipeline.discover import discover_series
from tarso_pipeline.io import load_forcing
from tarso_pipeline.paths import forcing_path
from tarso_pipeline.train import (
    TrainingContext,
    make_run_id,
    rows_to_frame,
    spark_schema,
    train_one,
)

CATALOG = dbutils.widgets.get("catalog")
SCHEMA = dbutils.widgets.get("schema")
OUTPUT_TABLE = f"{CATALOG}.{SCHEMA}.{dbutils.widgets.get('output_table')}"

INPUT_ROOT = dbutils.widgets.get("input_root").strip()
SURVEYS = [s.strip() for s in dbutils.widgets.get("surveys").split(",") if s.strip()]
MODEL_DIR = dbutils.widgets.get("model_dir").strip() or None

STATION = int(dbutils.widgets.get("station"))
MIN_DAYS = int(dbutils.widgets.get("min_days"))
MIN_OBS = int(dbutils.widgets.get("min_obs"))
HEAD_COLUMN = dbutils.widgets.get("head_column")
RESAMPLE_RULE = dbutils.widgets.get("resample_rule")
RESAMPLE_METHOD = dbutils.widgets.get("resample_method")
VALIDATION_FILTER = dbutils.widgets.get("validation_filter") == "true"
TMIN_CAL = dbutils.widgets.get("tmin_cal").strip() or None
TMAX_CAL = dbutils.widgets.get("tmax_cal").strip() or None
RUN_ID = dbutils.widgets.get("run_id").strip() or make_run_id()

if not INPUT_ROOT:
    raise ValueError("input_root is required, e.g. /Volumes/<catalog>/<schema>/<vol>/input_files")

print(f"run_id     {RUN_ID}")
print(f"input      {INPUT_ROOT}  surveys={SURVEYS or 'ALL'}")
print(f"models     {MODEL_DIR or '(not written)'}")
print(f"table      {OUTPUT_TABLE}")
print(f"filters    min_days={MIN_DAYS}  min_obs={MIN_OBS} (validated only)")
print(f"pastas     {ps.__version__}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 1. Discover the series

# COMMAND ----------

discovered = discover_series(INPUT_ROOT, surveys=SURVEYS or None)
print(f"{len(discovered)} series across {discovered['survey'].nunique()} survey(s)")
display(discovered)

if discovered.empty:
    raise ValueError(f"No head CSVs found under {INPUT_ROOT}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 2. Load the forcing
# MAGIC
# MAGIC One file, both variables — `load_forcing` picks the column. These are small and
# MAGIC every task needs them, so they are built once here and broadcast with the
# MAGIC context rather than re-read per series.

# COMMAND ----------

FORCING_FILE = str(forcing_path(STATION))
PREC_COLUMN, EVAP_COLUMN = "precipitation_mm", "makkink_mm"

precipitation = load_forcing(FORCING_FILE, PREC_COLUMN)
evaporation = load_forcing(FORCING_FILE, EVAP_COLUMN)

print(f"forcing    {FORCING_FILE}")
print(f"           {len(precipitation)} days, "
      f"{precipitation.index.min().date()} -> {precipitation.index.max().date()}")

# The forcing must cover the heads, or Pastas extrapolates into nothing.
heads_end = pd.Timestamp.today().normalize()
if precipitation.index.max() < heads_end - pd.Timedelta(days=30):
    print(f"           NOTE: forcing ends {precipitation.index.max().date()}; "
          "series extending past that will calibrate on a shorter window")

context = TrainingContext(
    run_id=RUN_ID,
    precipitation=precipitation,
    evaporation=evaporation,
    prec_file=FORCING_FILE,
    prec_column=PREC_COLUMN,
    evap_file=FORCING_FILE,
    evap_column=EVAP_COLUMN,
    model_dir=MODEL_DIR,
    head_column=HEAD_COLUMN,
    validation_filter=VALIDATION_FILTER,
    resample_rule=RESAMPLE_RULE,
    resample_method=RESAMPLE_METHOD,
    min_obs=MIN_OBS,
    min_days=MIN_DAYS,
    tmin_cal=TMIN_CAL,
    tmax_cal=TMAX_CAL,
)

# COMMAND ----------

# MAGIC %md
# MAGIC ## 3. Train
# MAGIC
# MAGIC Pastas and the scipy solver under it are single-threaded, CPU-bound and pure
# MAGIC Python, so one row per series distributes well. `train_one` catches inside the
# MAGIC worker and returns the failure as data — nothing here can abort the run.
# MAGIC
# MAGIC Set `distribute` to False to run on the driver instead, which is easier to debug
# MAGIC and fine for a modest series count.
# MAGIC
# MAGIC > The worker re-adds `src` to `sys.path` itself: executors do not inherit the
# MAGIC > driver's. If you prefer, `%pip install -e <repo>` installs the package on every
# MAGIC > node and makes that unnecessary — but `%pip` restarts Python, so it has to run
# MAGIC > in the install cell at the top, not here.

# COMMAND ----------

distribute = len(discovered) > 8

if not distribute:
    print(f"training {len(discovered)} series on the driver")
    rows = [
        train_one(r.file_path, r.survey, r.series_name, context)
        for r in discovered.itertuples()
    ]
    results = rows_to_frame(rows)
else:
    print(f"training {len(discovered)} series distributed")
    broadcast_context = sc.broadcast(context)
    src_path = SRC  # captured by value; the driver's sys.path is not the executors'

    def train_partition(iterator):
        # Executors do not inherit the driver's sys.path, and this must happen
        # before the broadcast value is deserialised, because unpickling
        # TrainingContext imports tarso_pipeline.train.
        import sys as _sys

        if src_path not in _sys.path:
            _sys.path.insert(0, src_path)
        import matplotlib as _matplotlib

        _matplotlib.use("Agg")
        from tarso_pipeline.train import rows_to_frame, train_one

        ctx = broadcast_context.value
        for pdf in iterator:
            yield rows_to_frame(
                [
                    train_one(r.file_path, r.survey, r.series_name, ctx)
                    for r in pdf.itertuples()
                ]
            )

    # One series per task: fits vary wildly in cost, so small partitions keep the
    # tail from sitting behind one slow solver.
    spark_discovered = spark.createDataFrame(discovered).repartition(len(discovered))
    results = spark_discovered.mapInPandas(
        train_partition, schema=spark_schema()
    ).toPandas()

print(f"done: {results['status'].value_counts().to_dict()}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 4. Persist
# MAGIC
# MAGIC Append-only, tagged with `run_id`: a run is never overwritten in place, so two
# MAGIC runs over the same series are comparable rather than destructive.

# COMMAND ----------

spark.sql(f"CREATE SCHEMA IF NOT EXISTS {CATALOG}.{SCHEMA}")

results_spark = spark.createDataFrame(results).withColumn(
    "ingested_at", F.current_timestamp()
)
(
    results_spark.write.format("delta")
    .mode("append")
    .option("mergeSchema", "true")
    .saveAsTable(OUTPUT_TABLE)
)
print(f"appended {len(results)} rows to {OUTPUT_TABLE} as run {RUN_ID}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Run summary
# MAGIC
# MAGIC `rsq` / `nse` around 0.7–0.95 is a healthy fit. Anything far below that is worth
# MAGIC looking at even though it "succeeded".

# COMMAND ----------

summary = (
    results.groupby("status")
    .agg(series=("series", "count"), median_rsq=("rsq", "median"))
    .reset_index()
)
display(summary)

display(
    results[
        ["series", "survey", "status", "length_series", "span_days", "rsq", "nse",
         "rmse", "response_tmax_95", "error"]
    ].sort_values(["status", "rsq"])
)

# COMMAND ----------

failed = results[results["status"] == "error"]
if not failed.empty:
    print(f"{len(failed)} series failed:")
    for row in failed.itertuples():
        print(f"  {row.series}: {row.error}")

poor = results[(results["status"] == "successful") & (results["rsq"] < 0.5)]
if not poor.empty:
    print(f"\n{len(poor)} fitted but with rsq < 0.5 (healthy is ~0.7-0.95):")
    for row in poor.itertuples():
        print(f"  {row.series}: rsq={row.rsq:.2f}  span={row.span_days}d")
