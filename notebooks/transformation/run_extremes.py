# Databricks notebook source
# /// script
# [tool.databricks.environment]
# environment_version = "6"
# ///
# MAGIC %md
# MAGIC # Stage 2: synthetic extremes and return periods
# MAGIC
# MAGIC Takes the calibrated `.pas` models stage 2 produced and turns each into a
# MAGIC ~704-year synthetic sample of annual groundwater extremes, then fits
# MAGIC extreme-value distributions and reports return levels.
# MAGIC
# MAGIC ### What this is, and is not
# MAGIC
# MAGIC It is **simulation, not extrapolation**. Each `.pas` is loaded, the two stresses
# MAGIC in its Tarso stressmodel are replaced with one IMPREX ensemble member's forcing,
# MAGIC and `simulate()` is called. Fitted parameters are untouched.
# MAGIC
# MAGIC The model is the only calibrated thing here. The forcing is synthetic, the heads
# MAGIC are synthetic, and a return period is a statement about **the model's response to
# MAGIC a 50-year climate sampled 16 times** - not a measurement.
# MAGIC
# MAGIC ### The three rules that make the pool valid
# MAGIC
# MAGIC 1. **Members are pooled at the extremes, never in time.** Concatenating the 16
# MAGIC    daily series would invent continuity across every join, and the model's
# MAGIC    multi-year memory would carry a transition no climate produced.
# MAGIC 2. **The block is a hydrological year (April-March).** A calendar block cuts a
# MAGIC    Dutch winter in half, letting one winter supply the maximum of two adjacent
# MAGIC    blocks - exactly the dependence a GEV assumes away.
# MAGIC 3. **Partial blocks are dropped** (< 90% of a year). The maximum over three
# MAGIC    months is not an annual maximum.
# MAGIC
# MAGIC Expect **704 blocks per tail**: 16 members x 44 years, 1956-1999. 800 means the
# MAGIC partial-block filter is not running; 16 440 means the members were concatenated.
# MAGIC
# MAGIC ### Trimming
# MAGIC
# MAGIC `exclude_first` defaults to **450**, leaving 254 of 704. Trimming changes what the
# MAGIC distribution *is*: what remains is conditional on clearing a threshold, so a
# MAGIC T-year event is the `1/(lam*T)` quantile, not `1/T`. That correction is applied.
# MAGIC
# MAGIC > **With 450 trimmed, lam = 0.361 and the smallest computable return period is
# MAGIC > T = 2.77.** T=2 is reported as null, not as a wrong number.

# COMMAND ----------

dbutils.widgets.text("source_root", "", "Source root (blank = find pyproject.toml)")
dbutils.widgets.text("volume_base",
                     "/Volumes/waterkeringbeheer_prd/misc_files/misc_files/pastas_pipeline",
                     "Volume prefix")
dbutils.widgets.text("run_folder", "", "Run folder (blank = run_yy_mm_dd today)")
dbutils.widgets.text("imprex_dir", "", "IMPREX dir (blank = <volume>/knmi_forcing)")
dbutils.widgets.text("location", "berkhout", "IMPREX location")

dbutils.widgets.text("forcing_units", "m", "Forcing units (match stage 2)")
dbutils.widgets.text("warmup_years", "5", "Spin-up years dropped per member")
dbutils.widgets.text("exclude_first", "450", "Least-extreme values trimmed per tail")
dbutils.widgets.text("t_list", "2 5 10 25 50 100 250 500 1000", "Return periods")
dbutils.widgets.text("limit_models", "0", "Max models (0 = all)")

dbutils.widgets.text("bootstrap_replicates", "200", "Shape bootstrap (family choice)")
dbutils.widgets.text("confidence_replicates", "500", "Bootstrap for confidence_plots")
dbutils.widgets.dropdown("make_plots", "true", ["true", "false"], "Make plots?")
dbutils.widgets.dropdown("write_tables", "true", ["true", "false"], "Write Delta tables?")
dbutils.widgets.text("output_catalog", "waterkeringbeheer_prd", "Output catalog")
dbutils.widgets.text("output_schema", "jvanruitenbeek", "Output schema")
dbutils.widgets.text("metrics_table",
                     "waterkeringbeheer_prd.jvanruitenbeek.pastas_fit_metrics",
                     "Stage-2 metrics (to recover the full series_id)")
dbutils.widgets.text("run_id", "", "Run id (blank = UTC timestamp)")

# COMMAND ----------

# MAGIC %pip install "pastas==1.12.*"
# MAGIC %restart_python

# COMMAND ----------

import os
import sys
from datetime import datetime, timezone


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
import pastas as ps
from pyspark.sql import functions as F

from tarso_pipeline.evt_report import TAILS
from tarso_pipeline.extremes_run import discover_models, run_model
from tarso_pipeline.imprex import discover_members
from tarso_pipeline.paths import (
    BIAS_LOG_FILE,
    EVT_SELECTION_FILE,
    EXTREMES_DIRS,
    TAIL_PARAMETERS_FILE,
    extremes_dir,
    extremes_summary_file,
    run_dir,
)

VOLUME_BASE = dbutils.widgets.get("volume_base").strip().rstrip("/")
RUN_DIR = run_dir(VOLUME_BASE, name=dbutils.widgets.get("run_folder").strip() or None)
IMPREX_DIR = dbutils.widgets.get("imprex_dir").strip() or f"{VOLUME_BASE}/knmi_forcing"
LOCATION = dbutils.widgets.get("location").strip()

UNITS = dbutils.widgets.get("forcing_units").strip()
WARMUP_YEARS = int(dbutils.widgets.get("warmup_years"))
EXCLUDE_FIRST = int(dbutils.widgets.get("exclude_first"))
T_LIST = tuple(int(t) for t in dbutils.widgets.get("t_list").split())
LIMIT_MODELS = int(dbutils.widgets.get("limit_models"))

REPLICATES = int(dbutils.widgets.get("bootstrap_replicates"))
CONFIDENCE_REPLICATES = int(dbutils.widgets.get("confidence_replicates"))
MAKE_PLOTS = dbutils.widgets.get("make_plots") == "true"
WRITE_TABLES = dbutils.widgets.get("write_tables") == "true"
CATALOG = dbutils.widgets.get("output_catalog").strip()
SCHEMA = dbutils.widgets.get("output_schema").strip()
RUN_ID = (
    dbutils.widgets.get("run_id").strip()
    or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
)

METRICS_TABLE = dbutils.widgets.get("metrics_table").strip()
SELECTION_TABLE = f"{CATALOG}.{SCHEMA}.pastas_evt_selection"
SUMMARY_TABLE = f"{CATALOG}.{SCHEMA}.pastas_extremes_summary"

print(f"run_id        {RUN_ID}")
print(f"run folder    {RUN_DIR}")
print(f"imprex        {IMPREX_DIR}  ({LOCATION})")
print(f"units         {UNITS}   warmup {WARMUP_YEARS}y   trim {EXCLUDE_FIRST}")
print(f"T list        {T_LIST}")
print(f"pastas        {ps.__version__}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## The models and the ensemble

# COMMAND ----------

models = discover_models(RUN_DIR)
if LIMIT_MODELS:
    models = models.head(LIMIT_MODELS)
print(f"{len(models)} model(s) in {RUN_DIR}/pas_files")

# The .pas filename only carries the truncated name, so the full 64-character
# meetreeks_id is recovered from the stage-2 metrics table. Anything unmatched
# falls back to the truncated name rather than dropping the model.
if spark.catalog.tableExists(METRICS_TABLE):
    lookup = (
        spark.table(METRICS_TABLE)
        .select("series_name", "series_id")
        .distinct()
        .toPandas()
    )
    models = models.merge(lookup, on="series_name", how="left")
else:
    print(f"NOTE: {METRICS_TABLE} not found; series_id falls back to series_name")
    models["series_id"] = None

unmatched = int(models["series_id"].isna().sum())
models["series_id"] = models["series_id"].fillna(models["series_name"])
print(f"full series_id recovered for {len(models) - unmatched}/{len(models)} model(s)"
      + (f"; {unmatched} fell back to the truncated name" if unmatched else ""))
display(models)

members = discover_members(IMPREX_DIR, LOCATION)
print(f"\n{len(members)} ensemble member(s) in {IMPREX_DIR}")
if not members:
    raise FileNotFoundError(
        f"No IMPREX members under {IMPREX_DIR} for location '{LOCATION}'. Expected "
        f"imprex_{LOCATION}_ens*_daily_precipitation.csv with evaporation partners."
    )
if len(members) < 16:
    print(f"NOTE: {len(members)} members, not 16 - the pooled sample will be "
          f"~{len(members) * 44} blocks per tail, not 704.")
for kind in EXTREMES_DIRS:
    os.makedirs(extremes_dir(RUN_DIR, kind), exist_ok=True)
print(f"\nartefact folders ready under {RUN_DIR}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Simulate, pool, fit
# MAGIC
# MAGIC Failures are per (model, member): a lost member is recorded and the rest
# MAGIC continue, because a model that loses some members still fits on the remainder.

# COMMAND ----------

summaries, selections, parameters = [], [], []

for item in models.itertuples():
    print(f"\n{item.series_name}")
    result = run_model(
        model_path=item.model_path,
        series_id=item.series_id,
        series_name=item.series_name,
        members=members,
        run_directory=RUN_DIR,
        run_id=RUN_ID,
        units=UNITS,
        warmup_years=WARMUP_YEARS,
        exclude_first=EXCLUDE_FIRST,
        t_list=T_LIST,
        replicates=REPLICATES,
        confidence_replicates=CONFIDENCE_REPLICATES,
        make_plots=MAKE_PLOTS,
    )
    summaries.append(result.summary)
    selections.extend(result.selection)
    parameters.extend(result.parameters)

    s = result.summary
    print(f"   status={s['status']}  members={s['n_members']}  "
          f"dry={s['n_dry']} wet={s['n_wet']}  years {s['first_year']}..{s['last_year']}")
    for prefix, tail in (("dr", "dry"), ("wet", "wet")):
        if s.get(f"{prefix}_family"):
            print(f"   {tail:4} {s[f'{prefix}_family']:<4} {s[f'{prefix}_verdict']:<20}"
                  f"shape={s[f'{prefix}_shape']:+.3f}+-{s[f'{prefix}_shape_se']:.3f}  "
                  f"T100={s[f'{prefix}_gev_T100']}")
        else:
            print(f"   {tail:4} no family: {s.get(f'{prefix}_verdict')}")
    if s["failure_detail"]:
        print(f"   failures: {s['failure_detail'][:160]}")

summary_frame = pd.DataFrame(summaries)
selection_frame = pd.DataFrame(selections)
parameter_frame = pd.DataFrame(parameters)
print(f"\n{len(summary_frame)} model(s): "
      f"{summary_frame['status'].value_counts().to_dict()}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Run-level roll-ups
# MAGIC
# MAGIC `evt_selection` is what tells anyone **which column of the return table to
# MAGIC read**; without it the choice is unauditable.

# COMMAND ----------

selection_path = os.path.join(RUN_DIR, EVT_SELECTION_FILE)
parameters_path = os.path.join(RUN_DIR, TAIL_PARAMETERS_FILE)
summary_path = extremes_summary_file(RUN_DIR)

selection_frame.to_csv(selection_path, index=False)
parameter_frame.to_csv(parameters_path, index=False)
summary_frame.to_csv(summary_path, index=False)
print(f"wrote {selection_path}")
print(f"wrote {parameters_path}")
print(f"wrote {summary_path}")

display(selection_frame)

# COMMAND ----------

display(
    summary_frame[
        ["series_name", "status", "n_members", "n_dry", "n_wet",
         "first_year", "last_year", "dr_family", "dr_verdict", "dr_gev_T100",
         "wet_family", "wet_verdict", "wet_gev_T100", "review_flag"]
    ]
)

# COMMAND ----------

# MAGIC %md
# MAGIC ## Delta tables
# MAGIC
# MAGIC Appended with the run id, so runs accumulate and join to `pastas_fit_metrics`
# MAGIC on `series_name`.

# COMMAND ----------

if not WRITE_TABLES:
    print("write_tables=false; nothing written")
else:
    spark.sql(f"CREATE SCHEMA IF NOT EXISTS {CATALOG}.{SCHEMA}")
    for frame, table in ((selection_frame, SELECTION_TABLE),
                         (summary_frame, SUMMARY_TABLE)):
        if frame.empty:
            print(f"nothing to write to {table}")
            continue
        (
            spark.createDataFrame(frame.astype(object).where(pd.notna(frame), None))
            .withColumn("written_at", F.current_timestamp())
            .write.format("delta").mode("append").option("mergeSchema", "true")
            .saveAsTable(table)
        )
        print(f"appended {len(frame)} rows to {table}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## What to quote
# MAGIC
# MAGIC **T=10** is stable to a couple of centimetres. **T=100** to about 0.1 m. Quote
# MAGIC those two.
# MAGIC
# MAGIC **T=1000 is extrapolation regardless** - 704 blocks reach an empirical return
# MAGIC period of ~705 years, so a 1000-year level is beyond anything in the pool and the
# MAGIC three families disagree by a median 0.30 m and sometimes far more. Treat it as
# MAGIC indicative.
# MAGIC
# MAGIC A `degenerate` verdict on a wet tail means the model's `dmax` ceiling is capping
# MAGIC the head, not that the fit failed.

# COMMAND ----------

flagged = summary_frame[summary_frame["review_flag"].astype(bool)]
if flagged.empty:
    print("no tails flagged for review")
else:
    print(f"{len(flagged)} model(s) flagged for review:")
    for row in flagged.itertuples():
        for prefix, tail in (("dr", "dry"), ("wet", "wet")):
            verdict = getattr(row, f"{prefix}_verdict", None)
            if getattr(row, f"{prefix}_review_flag", False):
                print(f"  {row.series_name} [{tail}]: {verdict}")

print("\nverdict distribution:")
for prefix, tail in (("dr", "dry"), ("wet", "wet")):
    counts = summary_frame[f"{prefix}_verdict"].value_counts().to_dict()
    print(f"  {tail}: {counts}")
