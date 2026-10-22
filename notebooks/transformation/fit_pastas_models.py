# Databricks notebook source
# /// script
# [tool.databricks.environment]
# environment_version = "6"
# ///
# MAGIC %md
# MAGIC # Fit Tarso models for the manifest series
# MAGIC
# MAGIC Reads the manifest, fits one model per series, and produces three outputs under
# MAGIC the same volume prefix, plus one Delta row per fit.
# MAGIC
# MAGIC ```
# MAGIC <volume>/run_yy_mm_dd/
# MAGIC     pas_files/          series_<hash24>_<model>.pas
# MAGIC     results_files/      series_<hash24>_<model>_results.png
# MAGIC     diagnostics_files/  series_<hash24>_<model>_diagnostics.png
# MAGIC ```
# MAGIC
# MAGIC Plus one row per fit in
# MAGIC `waterkeringbeheer_prd.jvanruitenbeek.pastas_fit_metrics`.
# MAGIC
# MAGIC Artefacts are grouped by **kind**, not by series, so all 20 `.pas` files sit
# MAGIC together and all 20 plots sit together. The model type is in the filename
# MAGIC because one series can be fitted with more than one, and the folders are shared
# MAGIC across series - without it a `recharge` run would overwrite the `tarso` one.
# MAGIC
# MAGIC > Two runs on the **same day** share a run folder and overwrite each other's
# MAGIC > artefacts, while `pastas_fit_metrics` keeps both rows. Set the `run_folder`
# MAGIC > widget (e.g. `run_26_10_15_b`) to keep them apart.
# MAGIC
# MAGIC Both paths are recorded as columns (`model_path`, `plot_path`), so nothing
# MAGIC downstream has to reconstruct them.
# MAGIC
# MAGIC ### Scale
# MAGIC
# MAGIC `limit_series` defaults to **0 = the whole manifest**, which is every series
# MAGIC spanning at least `min_days` (365). Set it to **1** to validate the chain after
# MAGIC any change - the volume write is probed first either way, so a missing grant
# MAGIC fails in seconds rather than after a long fit.
# MAGIC
# MAGIC Fits run **sequentially on the driver**, so wall-clock time is
# MAGIC `series x len(model_types) x seconds-to-minutes each`. Check the count the
# MAGIC manifest reports before launching an uncapped run, and drop `model_types` to
# MAGIC `tarso` alone to halve it.
# MAGIC
# MAGIC ### Forcing units
# MAGIC
# MAGIC KNMI publishes millimetres; Pastas is given **metres per day**
# MAGIC (`forcing_units=m`, mm / 1000), matching the head in metres NAP. The original
# MAGIC repo passed millimetres straight through - the choice only rescales the gain
# MAGIC parameters, but it makes the two conventions incomparable, so it is recorded on
# MAGIC every row. Set `forcing_units=mm` to reproduce the old behaviour.
# MAGIC
# MAGIC ### Warmup
# MAGIC
# MAGIC Pastas needs forcing over the calibration window **and** the warmup before it.
# MAGIC Coverage is checked per series and the outcome stated - the warmup is shortened
# MAGIC deliberately and reported, never left for Pastas to resolve silently.
# MAGIC
# MAGIC Station 249 Berkhout starts **1999-03-23**. The series here run from 2022, so
# MAGIC there are ~8300 days of forcing before them and the full 3650-day warmup fits
# MAGIC comfortably.
# MAGIC
# MAGIC The other end is routine: KNMI publishes 1-2 days behind, so a series running
# MAGIC to today always overshoots the forcing. `tmax` is trimmed back to the last
# MAGIC forcing day and the drop is reported and recorded (`calibration_trimmed`,
# MAGIC `days_trimmed_from_end`). Refreshing the forcing recovers those days.
# MAGIC
# MAGIC ### `validatie_status` is ignored
# MAGIC
# MAGIC By decision, not oversight: every row with a `waarde` is used, whatever its
# MAGIC review status. Revisit once the status values are understood.
# MAGIC
# MAGIC ### Failures are rows
# MAGIC
# MAGIC `fit_series` never raises: a failed series gets `success=false` and the message.
# MAGIC A run where 18 of 20 succeed is a useful result; one that dies on series 3 is not.

# COMMAND ----------

dbutils.widgets.text("source_root", "", "Source root (blank = find pyproject.toml)")

dbutils.widgets.text("source_table", "gold_prd.hl_grondwaterpeilmeting.meting", "Source")
dbutils.widgets.text("manifest_table",
                     "waterkeringbeheer_prd.jvanruitenbeek.pastas_series_manifest",
                     "Manifest table")
dbutils.widgets.text("metrics_table",
                     "waterkeringbeheer_prd.jvanruitenbeek.pastas_fit_metrics",
                     "Metrics table")
dbutils.widgets.text("volume_base",
                     "/Volumes/waterkeringbeheer_prd/misc_files/misc_files/pastas_pipeline",
                     "Volume prefix")
dbutils.widgets.text("forcing_dir", "", "Forcing dir (blank = <volume>/knmi_forcing)")

dbutils.widgets.text("station", "249", "KNMI station")
dbutils.widgets.dropdown("forcing_units", "m", ["m", "mm"], "Forcing units")
dbutils.widgets.text("warmup_days", "3650", "Warmup in days")

dbutils.widgets.multiselect("model_types", "tarso", ["tarso", "recharge"], "Model types")
dbutils.widgets.text("limit_series", "0", "Cap on series (0 = the whole manifest)")
dbutils.widgets.text("run_folder", "", "Run folder (blank = run_yy_mm_dd)")

dbutils.widgets.text("dedup_method", "mean", "Collapse duplicate timestamps with")
dbutils.widgets.text("resample_rule", "D", "Resample rule")
dbutils.widgets.text("resample_method", "mean", "Resample method")
dbutils.widgets.text("min_obs", "100", "Minimum observations after cleaning")
dbutils.widgets.text("min_days", "365", "Minimum span after cleaning")
dbutils.widgets.text("run_id", "", "Run id (blank = UTC timestamp)")
dbutils.widgets.dropdown("write_metrics", "true", ["true", "false"], "Write metrics?")
dbutils.widgets.dropdown("save_plots", "true", ["true", "false"], "Save result plots?")

# COMMAND ----------

# MAGIC %pip install "pastas==1.12.*"
# MAGIC %restart_python

# COMMAND ----------

import os
import sys
from datetime import datetime, timezone


def _resolve_source_root(given: str) -> str:
    """The repo's ``src``: an explicit parameter wins, discovery is the fallback.

    Walking up for pyproject.toml works in a Git folder but not when a Job task
    runs from another working directory, so the widget is the reliable route and
    the walk is only a convenience for interactive runs.
    """
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
                f"No pyproject.toml above {os.getcwd()} and source_root was not set. "
                "Set the source_root widget to the repo path, e.g. "
                "/Workspace/Shared/Projecten/databricks_pastas_pipeline"
            )
        current = parent


SRC = _resolve_source_root(dbutils.widgets.get("source_root").strip())
if SRC not in sys.path:
    sys.path.insert(0, SRC)
print(f"importing tarso_pipeline from {SRC}")

import matplotlib

matplotlib.use("Agg")  # no display on a cluster; must precede pyplot

import pandas as pd
import pastas as ps
from pyspark.sql import functions as F

from tarso_pipeline.fit import fit_series
from tarso_pipeline.fit_metrics import rows_to_frame, spark_schema
from tarso_pipeline.forcing import check_coverage, forcing_file, load_forcing_pair
from tarso_pipeline.meting import series_rows_sql
from tarso_pipeline.paths import ARTEFACT_DIRS, artefact_dir, run_dir, series_name_from_id

SOURCE = dbutils.widgets.get("source_table").strip()
MANIFEST = dbutils.widgets.get("manifest_table").strip()
METRICS = dbutils.widgets.get("metrics_table").strip()
VOLUME_BASE = dbutils.widgets.get("volume_base").strip().rstrip("/")
FORCING_DIR = dbutils.widgets.get("forcing_dir").strip() or f"{VOLUME_BASE}/knmi_forcing"

STATION = dbutils.widgets.get("station").strip()
FORCING_UNITS = dbutils.widgets.get("forcing_units")
WARMUP_DAYS = int(dbutils.widgets.get("warmup_days"))

MODEL_TYPES = [m for m in dbutils.widgets.get("model_types").split(",") if m.strip()]
LIMIT_SERIES = int(dbutils.widgets.get("limit_series"))
DEDUP_METHOD = dbutils.widgets.get("dedup_method")
RESAMPLE_RULE = dbutils.widgets.get("resample_rule")
RESAMPLE_METHOD = dbutils.widgets.get("resample_method")
MIN_OBS = int(dbutils.widgets.get("min_obs"))
MIN_DAYS = int(dbutils.widgets.get("min_days"))
WRITE_METRICS = dbutils.widgets.get("write_metrics") == "true"
SAVE_PLOTS = dbutils.widgets.get("save_plots") == "true"
RUN_ID = (
    dbutils.widgets.get("run_id").strip()
    or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
)

FORCING_FILE = forcing_file(FORCING_DIR, STATION)
RUN_DIR = run_dir(VOLUME_BASE, name=dbutils.widgets.get("run_folder").strip() or None)

print(f"run_id       {RUN_ID}")
print(f"manifest     {MANIFEST}")
print(f"metrics      {METRICS}")
print(f"volume       {VOLUME_BASE}")
print(f"run folder   {RUN_DIR}")
print(f"forcing      {FORCING_FILE}  ({FORCING_UNITS})")
print(f"model types  {MODEL_TYPES}   series cap: {LIMIT_SERIES or 'none'}")
print(f"cleaning     dedup={DEDUP_METHOD}, resample={RESAMPLE_RULE}/{RESAMPLE_METHOD}")
print(f"pastas       {ps.__version__}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Confirm the volume is writable
# MAGIC
# MAGIC `WRITE VOLUME` is a separate grant from the table write. Better to fail here on
# MAGIC a throwaway file than twenty fits in.

# COMMAND ----------

probe = os.path.join(VOLUME_BASE, "__write_test.txt")
try:
    os.makedirs(VOLUME_BASE, exist_ok=True)
    with open(probe, "w") as handle:
        handle.write("ok")
    os.remove(probe)
    for kind in ARTEFACT_DIRS:
        os.makedirs(artefact_dir(RUN_DIR, kind), exist_ok=True)
    print(f"writable: {VOLUME_BASE}")
    print(f"run folder ready: {RUN_DIR}")
    for kind in ARTEFACT_DIRS:
        print(f"   {kind}")
except Exception as exc:
    raise RuntimeError(
        f"Cannot write to {VOLUME_BASE}: {type(exc).__name__}: {exc}\n"
        "Request WRITE VOLUME on misc_files, or point volume_base somewhere writable."
    ) from exc

# COMMAND ----------

# MAGIC %md
# MAGIC ## Forcing
# MAGIC
# MAGIC One CSV per station, filename derived from the station. Read with pandas: it is
# MAGIC small and feeds Pastas directly.

# COMMAND ----------

if not os.path.isfile(FORCING_FILE):
    raise FileNotFoundError(
        f"No forcing at {FORCING_FILE}. Upload knmi_forcing_{STATION}.csv there "
        f"(generate it with: python scripts/check_knmi.py --station {STATION} "
        "--backlog-start 1970-01-01), or point the forcing_dir widget elsewhere."
    )

precipitation, evaporation = load_forcing_pair(FORCING_FILE, units=FORCING_UNITS)
print(f"{len(precipitation):,} days, {precipitation.index.min().date()} -> "
      f"{precipitation.index.max().date()}")
print(f"mean precipitation {precipitation.mean():.5f} {FORCING_UNITS}/day, "
      f"mean Makkink {evaporation.mean():.5f} {FORCING_UNITS}/day")

# COMMAND ----------

# MAGIC %md
# MAGIC ## The work list

# COMMAND ----------

if not spark.catalog.tableExists(MANIFEST):
    raise RuntimeError(
        f"No manifest at {MANIFEST}. "
        "Run task 1 first: notebooks/ingestion/build_manifest.py with "
        "write_manifest=true -- it selects the series to fit and writes this "
        "table. Note pastas_series_verdict is a different table (the full "
        "census, written by explore_meting.py) and is not the work list."
    )

selected = (
    spark.table(MANIFEST)
    .orderBy(F.col("span_days").desc(), F.col("n_values").desc())
)
# 0 means the whole manifest. Longest-first ordering is applied before the cap,
# so a capped run is a prefix of the full one rather than an arbitrary sample.
if LIMIT_SERIES:
    selected = selected.limit(LIMIT_SERIES)
work = selected.toPandas()
print(f"{len(work)} series x {len(MODEL_TYPES)} model type(s) = "
      f"{len(work) * len(MODEL_TYPES)} fits")
display(work)

# COMMAND ----------

# MAGIC %md
# MAGIC ## Fit
# MAGIC
# MAGIC Scale out with a Job "For each" task over `meetreeks_id` once one series is known
# MAGIC good - that gives per-series retries without restructuring any of this.

# COMMAND ----------

rows = []
for item in work.itertuples():
    raw = spark.sql(series_rows_sql(SOURCE, item.meetreeks_id)).toPandas()
    # The hash is the only name available; shortened for readable folders, with
    # the full meetreeks_id kept on the row.
    series_name = series_name_from_id(item.meetreeks_id)
    print(f"\n{series_name}  ({len(raw):,} raw rows, manifest span {item.span_days}d)")

    if not raw.empty:
        stamps = pd.DatetimeIndex(
            pd.to_datetime(raw["tijdstempel"], errors="coerce", utc=True)
        ).tz_convert(None)
        head_probe = pd.Series(1.0, index=stamps).dropna()
        coverage = check_coverage(precipitation, head_probe, warmup_days=WARMUP_DAYS)
        print(f"   {coverage.describe()}")

    for model_type in MODEL_TYPES:
        row = fit_series(
            meetreeks_id=item.meetreeks_id,
            series_name=series_name,
            raw=raw,
            precipitation=precipitation,
            evaporation=evaporation,
            run_id=RUN_ID,
            model_type=model_type,
            run_directory=RUN_DIR,
            dedup_method=DEDUP_METHOD,
            resample_rule=RESAMPLE_RULE,
            resample_method=RESAMPLE_METHOD,
            min_obs=MIN_OBS,
            min_days=MIN_DAYS,
            warmup_days=WARMUP_DAYS,
            forcing_station=STATION,
            forcing_units=FORCING_UNITS,
            save_plot=SAVE_PLOTS,
        )
        rows.append(row)
        if row["success"]:
            print(f"   {model_type:9} OK   EVP={row['evp']:.1f}%  R2={row['r2']:.3f}  "
                  f"RMSE={row['rmse']:.3f}  nobs={row['nobs']}  "
                  f"(dedup removed {row['n_duplicate_timestamps']:,})")
            if row["calibration_trimmed"]:
                print(f"   {'':9} calibration trimmed: "
                      f"{row['tmin']} .. {row['tmax']} "
                      f"({row['days_trimmed_from_end']} day(s) past the forcing)")
            if SAVE_PLOTS and not row["plot_path"]:
                print(f"   {'':9} note: fit succeeded but the plot could not be saved")
        else:
            print(f"   {model_type:9} FAIL {row['error_message']}")

results = rows_to_frame(rows)
print(f"\n{int(results['success'].sum())} of {len(results)} fits succeeded")

# COMMAND ----------

display(
    results[["series_name", "model_type", "success", "nobs", "span_days",
             "evp", "r2", "rmse", "aicc", "bic", "obj", "nfev",
             "n_raw_rows", "n_duplicate_timestamps",
             "model_path", "plot_path", "diagnostics_path", "error_message"]]
)

# COMMAND ----------

# MAGIC %md
# MAGIC ## Persist
# MAGIC
# MAGIC Append with a distinct `run_id`, so fits accumulate and runs stay comparable.

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
        .select("run_id", "series_name", "model_type", "success",
                "evp", "r2", "rmse", "forcing_station", "forcing_units")
        .orderBy(F.col("fitted_at").desc())
        .limit(20)
    )
