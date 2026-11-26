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
# MAGIC <volume>/<prefix>/run_yy_mm_dd/
# MAGIC     pas_files/          series_<hash24>_<model>.pas
# MAGIC     results_files/      series_<hash24>_<model>_results.png
# MAGIC     diagnostics_files/  series_<hash24>_<model>_diagnostics.png
# MAGIC ```
# MAGIC
# MAGIC Plus one row per fit in `<prefix>_fit_metrics`, one row per model check
# MAGIC (Brakenhoff et al. 2022) per successful fit in `<prefix>_model_checks`, and the daily
# MAGIC observed + simulated head of every successful fit in `<prefix>_simulations` (what the
# MAGIC Streamlit app plots, so it never needs the `.pas` files). `<prefix>` is
# MAGIC `unvalid_pastas` or `valid_pastas`, from `data_basis`.
# MAGIC
# MAGIC #### Validated or unvalidated (`data_basis`)
# MAGIC
# MAGIC `unvalidated` reads the raw source `gold_prd.hl_grondwaterpeilmeting.meting`;
# MAGIC `validated` reads `waterkeringbeheer_prd.jvanruitenbeek.validated_meting`, the hours
# MAGIC approved in the data validation review with corrections applied (same columns, written
# MAGIC by `data_validation/databricks_code/notebooks/03_validated_to_delta.py`). Every table
# MAGIC name follows from it: `unvalid_pastas_<kind>` or `valid_pastas_<kind>`
# MAGIC (`tarso_pipeline.paths.output_table`). A table widget left blank takes that name; fill
# MAGIC one in only to override it.

# MAGIC
# MAGIC Artefacts are grouped by **kind**, not by series, so all 20 `.pas` files sit
# MAGIC together and all 20 plots sit together. The model type is in the filename
# MAGIC because one series is fitted with more than one, and the folders are shared
# MAGIC across series - without it a `linear_gamma` fit would overwrite the `tarso` one.
# MAGIC
# MAGIC ### Model types
# MAGIC
# MAGIC `model_types` defaults to the **extended set** (`tarso_pipeline.fit.EXTENDED_MODEL_TYPES`):
# MAGIC Tarso and the four Linear recharge models (`linear_exp`, `linear_gamma`, `linear_dexp`,
# MAGIC `linear_fourparam`), plus the Linear and bucket (`flex_*`) models with the Markermeer
# MAGIC level and air pressure (`*_lake_baro`), on the KNMI station and on the polder's KNMI
# MAGIC radar precipitation (`*_lake_baro_radar`), each also with a root-zone soil-moisture
# MAGIC bucket (`*_lake_baro_bucket`, `*_lake_baro_bucket_radar`: the deficit of a 150 mm bucket
# MAGIC from the model's own P and E, solved nested on the type without it). On the validated
# MAGIC Purmer/Beemster series lake + pressure took the passed models from 35 to 61 and the
# MAGIC bucket from 60 to 65. 29 types, so about twice the fit time of the 17 without the
# MAGIC bucket. The five standard types alone:
# MAGIC `tarso,linear_exp,linear_gamma,linear_dexp,linear_fourparam`.
# MAGIC
# MAGIC **Inputs** (`tarso_pipeline.inputs`) come from the forcing folder (`forcing_dir`), with the
# MAGIC repo's `pastas/data/forcing` as the fallback for a file that is not there:
# MAGIC `knmi_forcing_<station>.csv`, `radar_forcing_<polder>.csv`, `stress_lake_edam.csv`,
# MAGIC `stress_pressure_240.csv` (built locally by `scripts/build_forcing_sources.py`; the
# MAGIC cluster has no internet). A series' polder comes from the repo's `object_data.csv`
# MAGIC (dike code BE / PU / HH); radar types are skipped for a series outside those polders.
# MAGIC Every fit row records `precipitation_source` (`knmi_249`, `radar_purmer`, ...),
# MAGIC `evaporation_source` and `extra_stresses`.
# MAGIC
# MAGIC All are fitted per series; which one a series *uses* is decided after the extremes run
# MAGIC (`run_extremes` -> `<prefix>_model_selection`, `tarso_pipeline.selection`): the checks,
# MAGIC no runaway in the extremes, then the lowest AIC, a simpler model winning within 2 AIC.
# MAGIC `peterson_gamma` is available but off: it wins AIC and predicts unseen data worse.
# MAGIC It is fitted on mm/day, so it assumes `forcing_units=m` (it scales by 1000 itself).
# MAGIC
# MAGIC `holdout` (default 0.3) fits each model a second time on the first 70 % of the
# MAGIC observations and scores it on the last 30 % (`r2_holdout`): the selection uses it to
# MAGIC refuse a lower AIC bought with worse prediction. It doubles the fit time; 0 turns it off.
# MAGIC
# MAGIC `noise` adds a noise model (`ar1` or `arma`). Off by default: on the validated
# MAGIC series neither made the runs test pass (2-5 of 25), and both lowered the R2 of the
# MAGIC simulation. AIC is computed on the noise then, so never compare AIC across noise settings.
# MAGIC
# MAGIC > Two runs on the **same day** share a run folder and overwrite each other's
# MAGIC > artefacts, while `<prefix>_fit_metrics` keeps both rows. Set the `run_folder`
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
# MAGIC manifest reports before launching an uncapped run. The Linear models take about a
# MAGIC second each; Tarso, `linear_fourparam` and the bucket models a few. The extended set is
# MAGIC 17 types per series (11 outside the radar polders): about 3x the five standard ones.
# MAGIC
# MAGIC Results are **written every `write_every` series** (default 25), not only at the
# MAGIC end, so a crash loses at most one batch. To resume a run that stopped, rerun with the
# MAGIC same `run_id` (and `run_folder`): series that already have rows for that run are
# MAGIC skipped. `keep_simulation_runs` (default 3) keeps the daily simulations of the newest
# MAGIC three runs per series and model type: readers use the latest run anyway, and the table
# MAGIC would otherwise grow by every day of every fit of every run.
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
# MAGIC With `data_basis=unvalidated` every row with a `waarde` is used, whatever its source
# MAGIC status. To train on reviewed data, use `data_basis=validated`: that table holds only
# MAGIC the hours a person approved.
# MAGIC
# MAGIC ### Failures are rows
# MAGIC
# MAGIC `fit_series` never raises: a failed series gets `success=false` and the message.
# MAGIC A run where 18 of 20 succeed is a useful result; one that dies on series 3 is not.

# COMMAND ----------

dbutils.widgets.text("source_root", "", "Source root (blank = find pyproject.toml)")

dbutils.widgets.dropdown("data_basis", "unvalidated", ["unvalidated", "validated"], "Train on")
dbutils.widgets.text("source_table", "", "Source (blank = from data_basis)")
dbutils.widgets.text("manifest_table", "", "Manifest table (blank = from data_basis)")
dbutils.widgets.text("metrics_table", "", "Metrics table (blank = from data_basis)")
dbutils.widgets.text("checks_table", "", "Model checks table (blank = from data_basis)")
dbutils.widgets.text("simulations_table", "", "Simulations table (blank = from data_basis)")
dbutils.widgets.text("volume_base",
                     "/Volumes/waterkeringbeheer_prd/misc_files/misc_files/pastas_pipeline",
                     "Volume prefix")
dbutils.widgets.text("forcing_dir", "", "Forcing dir (blank = <volume>/knmi_forcing)")

dbutils.widgets.text("station", "249", "KNMI station")
dbutils.widgets.dropdown("forcing_units", "m", ["m", "mm"], "Forcing units")
dbutils.widgets.text("warmup_days", "3650", "Warmup in days")

# A text widget, as in run_extremes and export_results. Blank: [fit] model_types of
# config/model_settings.toml (the extended set when that is empty).
dbutils.widgets.text("model_types", "", "Model types (comma-separated; blank = config/model_settings.toml)")
dbutils.widgets.text("object_data", "", "object_data.csv for the polder per series (blank = the repo's)")
dbutils.widgets.dropdown("noise", "none", ["none", "ar1", "arma"], "Noise model")
dbutils.widgets.text("holdout", "", "Holdout share for r2_holdout (0 = off; blank = model_settings.toml)")
dbutils.widgets.text("limit_series", "0", "Cap on series (0 = the whole manifest)")
dbutils.widgets.text("run_folder", "", "Run folder (blank = run_yy_mm_dd)")

dbutils.widgets.text("dedup_method", "mean", "Collapse duplicate timestamps with")
dbutils.widgets.text("resample_rule", "D", "Resample rule")
dbutils.widgets.text("resample_method", "mean", "Resample method")
dbutils.widgets.text("min_obs", "100", "Minimum observations after cleaning")
dbutils.widgets.text("min_days", "", "Minimum span after cleaning (blank = model_settings.toml)")
dbutils.widgets.text("run_id", "", "Run id (blank = UTC timestamp)")
dbutils.widgets.dropdown("write_metrics", "true", ["true", "false"], "Write metrics?")
dbutils.widgets.dropdown("save_plots", "false", ["true", "false"],
                         "Fit plots for every model? (false: run_extremes draws the models in use)")
dbutils.widgets.text("write_every", "25", "Write results every N series (0 = only at the end)")
dbutils.widgets.text("keep_simulation_runs", "3", "Runs kept in <prefix>_simulations (0 = all)")

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
                "/Workspace/Shared/Projecten/databricks_grondwater_platform/pastas"
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

from tarso_pipeline.fit import CANDIDATE_MODEL_TYPES, MODEL_SPECS, fit_series
from tarso_pipeline.overrides import excludes, read_overrides
from tarso_pipeline.settings import load_settings
from tarso_pipeline.fit_metrics import rows_to_frame, spark_schema
from tarso_pipeline.forcing import check_coverage, forcing_file
from tarso_pipeline.inputs import OBJECT_DATA, InputLibrary, read_polders, source_label
from tarso_pipeline.meting import series_rows_sql
from tarso_pipeline.model_checks import rows_to_frame as check_rows_to_frame
from tarso_pipeline.model_checks import spark_schema as checks_spark_schema
from tarso_pipeline.fit import SIMULATION_COLUMNS
from tarso_pipeline.paths import (
    ARTEFACT_DIRS,
    artefact_dir,
    basis_volume,
    output_table,
    run_dir,
    series_name_from_id,
    source_table,
)

BASIS = dbutils.widgets.get("data_basis")
SOURCE = dbutils.widgets.get("source_table").strip() or source_table(BASIS)
MANIFEST = dbutils.widgets.get("manifest_table").strip() or output_table(BASIS, "series_manifest")
METRICS = dbutils.widgets.get("metrics_table").strip() or output_table(BASIS, "fit_metrics")
CHECKS = dbutils.widgets.get("checks_table").strip() or output_table(BASIS, "model_checks")
SIMULATIONS = dbutils.widgets.get("simulations_table").strip() or output_table(BASIS, "simulations")
VOLUME_BASE = dbutils.widgets.get("volume_base").strip().rstrip("/")
FORCING_DIR = dbutils.widgets.get("forcing_dir").strip() or f"{VOLUME_BASE}/knmi_forcing"

STATION = dbutils.widgets.get("station").strip()
FORCING_UNITS = dbutils.widgets.get("forcing_units")
WARMUP_DAYS = int(dbutils.widgets.get("warmup_days"))

SETTINGS = load_settings()
OVERRIDES = read_overrides()
print(f"settings {SETTINGS.source}; {len(OVERRIDES)} series override(s)")
MODEL_TYPES = ([m.strip() for m in dbutils.widgets.get("model_types").split(",") if m.strip()]
               or list(CANDIDATE_MODEL_TYPES))
_unknown = [m for m in MODEL_TYPES if m not in MODEL_SPECS]
if not MODEL_TYPES or _unknown:
    raise ValueError(f"model_types: unknown {_unknown or '(empty)'}; choose from {sorted(MODEL_SPECS)}")
NOISE = dbutils.widgets.get("noise")
_holdout = dbutils.widgets.get("holdout").strip()
HOLDOUT = float(_holdout) if _holdout else float(SETTINGS.fit.holdout)
LIMIT_SERIES = int(dbutils.widgets.get("limit_series"))
DEDUP_METHOD = dbutils.widgets.get("dedup_method")
RESAMPLE_RULE = dbutils.widgets.get("resample_rule")
RESAMPLE_METHOD = dbutils.widgets.get("resample_method")
MIN_OBS = int(dbutils.widgets.get("min_obs"))
MIN_DAYS = int(dbutils.widgets.get("min_days").strip() or SETTINGS.fit.min_days)
WRITE_METRICS = dbutils.widgets.get("write_metrics") == "true"
SAVE_PLOTS = dbutils.widgets.get("save_plots") == "true"
WRITE_EVERY = int(dbutils.widgets.get("write_every") or 0)
KEEP_SIMULATION_RUNS = int(dbutils.widgets.get("keep_simulation_runs") or 0)
RUN_ID = (
    dbutils.widgets.get("run_id").strip()
    or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
)

FORCING_FILE = forcing_file(FORCING_DIR, STATION)
PASTAS_ROOT = os.path.dirname(SRC.rstrip("/"))
REPO_FORCING = os.path.join(PASTAS_ROOT, "data", "forcing")
OBJECT_DATA_PATH = dbutils.widgets.get("object_data").strip() or os.path.join(os.path.dirname(PASTAS_ROOT), str(OBJECT_DATA))
# Run folders per data basis, so a validated and an unvalidated run never share one.
RUN_DIR = run_dir(basis_volume(VOLUME_BASE, BASIS), name=dbutils.widgets.get("run_folder").strip() or None)

print(f"basis        {BASIS}  (source {SOURCE})")
print(f"run_id       {RUN_ID}")
print(f"manifest     {MANIFEST}")
print(f"metrics      {METRICS}")
print(f"checks       {CHECKS}")
print(f"volume       {VOLUME_BASE}")
print(f"run folder   {RUN_DIR}")
print(f"forcing      {FORCING_FILE}  ({FORCING_UNITS})")
print(f"model types  {MODEL_TYPES}   series cap: {LIMIT_SERIES or 'none'}")
print(f"noise model  {NOISE}   holdout {HOLDOUT:g}")
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

if not os.path.isfile(FORCING_FILE) and not os.path.isfile(os.path.join(REPO_FORCING, f"knmi_forcing_{STATION}.csv")):
    raise FileNotFoundError(
        f"No forcing at {FORCING_FILE} nor in {REPO_FORCING}. Upload knmi_forcing_{STATION}.csv there "
        f"(generate it with: python scripts/check_knmi.py --station {STATION} "
        "--backlog-start 1970-01-01), or point the forcing_dir widget elsewhere."
    )

POLDERS = read_polders(OBJECT_DATA_PATH) if os.path.isfile(OBJECT_DATA_PATH) else {}
LIBRARY = InputLibrary(FORCING_DIR, station=STATION, units=FORCING_UNITS, fallback=REPO_FORCING, polders=POLDERS)
precipitation, evaporation = LIBRARY.station_forcing()
print(f"polder (for radar) known for {len(POLDERS)} series ({OBJECT_DATA_PATH})")
for name in ("knmi_forcing_" + STATION + ".csv", "radar_forcing_beemster.csv", "radar_forcing_purmer.csv",
             "radar_forcing_heerhugowaard.csv", "stress_lake_edam.csv", "stress_pressure_240.csv"):
    print(f"   {name:34} {LIBRARY.path(name)}{'' if LIBRARY.path(name).is_file() else '  (MISSING)'}")
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

# A rerun with the same run_id resumes: series that already have rows for it are skipped.
done = set()
if WRITE_METRICS and spark.catalog.tableExists(METRICS):
    done = {r.meetreeks_id for r in spark.sql(
        f"SELECT DISTINCT meetreeks_id FROM {METRICS} WHERE run_id = '{RUN_ID}'").collect()}
if done:
    print(f"resuming run {RUN_ID}: {len(done)} series already written, skipped")


def write_batch(batch_rows, batch_checks, batch_simulations):
    """Append one batch of fits to the three tables (same run_id): a crash loses one batch."""
    if not WRITE_METRICS or not batch_rows:
        return
    spark.createDataFrame(rows_to_frame(batch_rows), schema=spark_schema()).write.format("delta").mode(
        "append").option("mergeSchema", "true").saveAsTable(METRICS)
    if batch_checks:
        spark.createDataFrame(check_rows_to_frame(batch_checks), schema=checks_spark_schema()).write.format(
            "delta").mode("append").option("mergeSchema", "true").saveAsTable(CHECKS)
    if batch_simulations:
        simulations = pd.concat(batch_simulations, ignore_index=True)[SIMULATION_COLUMNS]
        (spark.createDataFrame(simulations.astype(object).where(pd.notna(simulations), None))
         .withColumn("datum", F.to_date("datum"))
         .withColumn("observed", F.col("observed").cast("double"))
         .withColumn("simulated", F.col("simulated").cast("double"))
         .write.format("delta").mode("append").option("mergeSchema", "true")
         .saveAsTable(SIMULATIONS))
    print(f"   -> wrote {len(batch_rows)} fits, {len(batch_checks)} checks, "
          f"{sum(len(f) for f in batch_simulations):,} simulated days (run {RUN_ID})")


rows = []          # every fit of this session, for the summaries below
check_rows = []    # likewise; fit_series appends to it
pending_rows, pending_checks, simulation_frames = [], [], []  # not written yet
fitted_series = 0
for item in work.itertuples():
    if item.meetreeks_id in done:
        continue
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

    checks_before = len(check_rows)
    for model_type in MODEL_TYPES:
        if not LIBRARY.applicable(model_type, item.meetreeks_id):
            continue  # a radar type for a series outside the radar polders
        if excludes(OVERRIDES, item.meetreeks_id, model_type):
            print(f"   {model_type}: excluded (config/series_overrides.csv)")
            continue
        inputs = LIBRARY.inputs(model_type, item.meetreeks_id)
        row = fit_series(
            meetreeks_id=item.meetreeks_id,
            series_name=series_name,
            raw=raw,
            precipitation=inputs.precipitation,
            evaporation=inputs.evaporation,
            stresses=inputs.stresses,
            precipitation_source=inputs.precipitation_source,
            evaporation_source=inputs.evaporation_source,
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
            noise=NOISE,
            holdout=HOLDOUT,
            check_rows=check_rows,
            simulation_frames=simulation_frames,
        )
        rows.append(row)
        pending_rows.append(row)
        if row["success"]:
            holdout_text = "" if row.get("r2_holdout") is None else f"  R2 holdout={row['r2_holdout']:.3f}"
            print(f"   {model_type:9} OK   [{source_label(inputs.precipitation_source)}]  "
                  f"EVP={row['evp']:.1f}%  R2={row['r2']:.3f}{holdout_text}  "
                  f"AIC={row['aic']:.1f}  k={row['n_param']}  RMSE={row['rmse']:.3f}  nobs={row['nobs']}  "
                  f"(dedup removed {row['n_duplicate_timestamps']:,})")
            failed = [c["check"] for c in check_rows
                      if c["series_name"] == series_name
                      and c["model_type"] == model_type and c["passed"] is not True]
            print(f"   {'':9} checks: "
                  + ("all passed" if not failed else f"not passed: {', '.join(failed)}"))
            if row["calibration_trimmed"]:
                print(f"   {'':9} calibration trimmed: "
                      f"{row['tmin']} .. {row['tmax']} "
                      f"({row['days_trimmed_from_end']} day(s) past the forcing)")
            if SAVE_PLOTS and not row["plot_path"]:
                print(f"   {'':9} note: fit succeeded but the plot could not be saved")
        else:
            print(f"   {model_type:9} FAIL {row['error_message']}")

    pending_checks.extend(check_rows[checks_before:])
    fitted_series += 1
    if WRITE_EVERY and fitted_series % WRITE_EVERY == 0:
        write_batch(pending_rows, pending_checks, simulation_frames)
        pending_rows, pending_checks, simulation_frames = [], [], []

write_batch(pending_rows, pending_checks, simulation_frames)  # the last, partial batch
pending_rows, pending_checks, simulation_frames = [], [], []
results = rows_to_frame(rows)
print(f"\n{int(results['success'].sum())} of {len(results)} fits succeeded")

# COMMAND ----------

display(
    results[["series_name", "model_type", "success", "nobs", "span_days",
             "evp", "r2", "rmse", "aicc", "bic", "obj", "nfev",
             "t95_level0_days", "t95_level1_days", "t95_upper_regime_days",
             "n_raw_rows", "n_duplicate_timestamps",
             "model_path", "plot_path", "diagnostics_path", "error_message"]]
)

# COMMAND ----------

# MAGIC %md
# MAGIC ## Model checks
# MAGIC
# MAGIC Brakenhoff et al. (2022) via `ps.check.checklist`: one row per check per fit
# MAGIC in `<prefix>_model_checks`. The table below counts every check, the runs test
# MAGIC included. The stamp `passed` that the model pick uses (`run_extremes` ->
# MAGIC `<prefix>_model_selection`, `tarso_pipeline.selection`) needs R2, response memory, gain
# MAGIC and parameter bounds only: without a noise model the runs test fails everywhere. A
# MAGIC check that could not be evaluated (`passed` NULL) counts as not passed.

# COMMAND ----------

checks = check_rows_to_frame(check_rows)
if checks.empty:
    print("no successful fits, so nothing was checked")
else:
    verdict = (
        checks.assign(ok=checks["passed"].eq(True))
        .groupby(["series_name", "model_type"])
        .agg(checks=("ok", "size"), passed=("ok", "sum"), all_passed=("ok", "all"))
        .reset_index()
    )
    print(f"{int(verdict['all_passed'].sum())} of {len(verdict)} fits pass every check")
    display(verdict)
    display(checks[["series_name", "model_type", "check", "statistic", "operator",
                    "threshold", "threshold_min", "threshold_max", "passed", "comment"]])

# COMMAND ----------

# MAGIC %md
# MAGIC ## Persist
# MAGIC
# MAGIC The fits were appended per batch with a distinct `run_id`, so fits accumulate and runs
# MAGIC stay comparable. Here: the summary, and old simulation runs pruned.

# COMMAND ----------

if not WRITE_METRICS:
    print("write_metrics=false; nothing written")
elif results.empty:
    print("no new rows (every series of this run was written already, or nothing to fit)")
else:
    print(f"{len(results)} fits of run {RUN_ID} written to {METRICS}, "
          f"{len(checks)} check rows to {CHECKS}, simulations to {SIMULATIONS}")
    if KEEP_SIMULATION_RUNS and spark.catalog.tableExists(SIMULATIONS):
        # Per series and model type, not per run: a capped test run (limit_series=1) must
        # not remove the simulations of every other series.
        spark.sql(f"""
            MERGE INTO {SIMULATIONS} t
            USING (SELECT meetreeks_id, model_type, run_id FROM (
                       SELECT meetreeks_id, model_type, run_id,
                              dense_rank() OVER (PARTITION BY meetreeks_id, model_type
                                                 ORDER BY run_id DESC) AS rang
                       FROM (SELECT DISTINCT meetreeks_id, model_type, run_id FROM {SIMULATIONS}))
                   WHERE rang > {KEEP_SIMULATION_RUNS}) o
            ON t.meetreeks_id = o.meetreeks_id AND t.model_type = o.model_type AND t.run_id = o.run_id
            WHEN MATCHED THEN DELETE""")
        print(f"{SIMULATIONS}: kept the newest {KEEP_SIMULATION_RUNS} run(s) per series and model type")

    display(
        spark.table(METRICS)
        .select("run_id", "series_name", "model_type", "success",
                "evp", "r2", "rmse", "precipitation_source", "evaporation_source", "extra_stresses",
                "forcing_units")
        .orderBy(F.col("fitted_at").desc())
        .limit(20)
    )
