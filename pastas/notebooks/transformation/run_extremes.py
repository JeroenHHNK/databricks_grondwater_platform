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
# MAGIC ### Validated or unvalidated (`data_basis`)
# MAGIC
# MAGIC `unvalidated` reads the raw source `gold_prd.hl_grondwaterpeilmeting.meting`;
# MAGIC `validated` reads `waterkeringbeheer_prd.jvanruitenbeek.validated_meting`, the hours
# MAGIC approved in the data validation review with corrections applied (same columns, written
# MAGIC by `data_validation/databricks_code/notebooks/03_validated_to_delta.py`). Every table
# MAGIC name follows from it: `unvalid_pastas_<kind>` or `valid_pastas_<kind>`
# MAGIC (`tarso_pipeline.paths.output_table`). Pick the same basis as the fit run whose
# MAGIC `.pas` files this reads.
# MAGIC
# MAGIC Writes `<prefix>_evt_selection`, `<prefix>_extremes_summary` and
# MAGIC `<prefix>_return_levels` (one row per tail x family x return period: what the app and
# MAGIC Boxplots read, so a chosen family is a WHERE, not a column name), per model: every
# MAGIC model type the fit stage wrote a `.pas` for (`model_types` limits it).
# MAGIC
# MAGIC Then `<prefix>_model_selection`: per series x model type the "passed" stamp, the
# MAGIC runaway check on these extremes and which model the series uses
# MAGIC (`tarso_pipeline.selection`): among the models that pass the checks (else all), no
# MAGIC runaway, not worse than Tarso on the holdout, the lowest AIC - a simpler model within
# MAGIC 2 AIC wins. A person can overrule it in the app (`pastas/model_choice/`).
# MAGIC
# MAGIC ### What this is, and is not
# MAGIC
# MAGIC It is **simulation, not extrapolation**. Each `.pas` is loaded, the two stresses
# MAGIC in its Tarso or Recharge stressmodel are replaced with one IMPREX ensemble member's forcing,
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
# MAGIC ### What is fitted, and what is quoted
# MAGIC
# MAGIC | | on | used for |
# MAGIC |---|---|---|
# MAGIC | `emp` | all ~704 years (plotting position) | **T <= 100**: ~7 exceedances at T=100, no distribution needed |
# MAGIC | `gpd` | the years above a threshold, lower bound at the threshold | **T > 100** if picked: a tail that flattens off |
# MAGIC | `exp` | the same, shape 0 | **T > 100** if picked: a tail that does not flatten off |
# MAGIC | `gev` | all years | comparison only |
# MAGIC
# MAGIC Nothing is chosen here. A person picks `gpd` or `exp` per tail, and a threshold, in the
# MAGIC app (*Pastas -> Familie kiezen*), from the return-period and threshold plots.
# MAGIC
# MAGIC ### The threshold
# MAGIC
# MAGIC `exclude_first` (default **450**, snapped to the nearest `kept_grid` value: 250 of 704 kept)
# MAGIC sets the default threshold, midway
# MAGIC between the last dropped and the first kept year. Above it a T-year event is the
# MAGIC `1/(lam*T)` quantile of the fit (lam = 250/704), not `1/T`; T=2 sits below the threshold
# MAGIC and is null. The GPD and exponential are also fitted for every `n_kept` of `kept_grid`
# MAGIC (`<prefix>_return_levels.n_kept`), and `threshold_plots/` shows the GPD shape and the
# MAGIC T=1000 level per threshold: where they stop changing, the threshold is high enough.
# MAGIC
# MAGIC ### What the numbers do not contain
# MAGIC
# MAGIC T=1000 is extrapolation whatever the family: report it as indicative. And every level
# MAGIC here takes the Tarso model as exact, while it was calibrated on a few years of heads;
# MAGIC that model uncertainty is not in any band (it would need simulations with sampled
# MAGIC parameters) and is probably larger than the difference between families.
# MAGIC
# MAGIC The **confidence plots** (bootstrap bands of GPD and exponential) are made by
# MAGIC `analysis/explore_shape_and_uncertainty` after this notebook (same `data_basis` and
# MAGIC `run_folder`); it reads the `pooled_extremes/` written below.

# COMMAND ----------

dbutils.widgets.text("source_root", "", "Source root (blank = find pyproject.toml)")
dbutils.widgets.dropdown("data_basis", "unvalidated", ["unvalidated", "validated"], "Train on")
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
dbutils.widgets.text("model_types", "", "Model types (blank = every .pas in the run folder)")
dbutils.widgets.dropdown("select_models", "true", ["true", "false"], "Write the model selection?")

dbutils.widgets.text("kept_grid", "100 150 200 250 300 350 400",
                     "Years above the threshold to scan (n_kept)")
dbutils.widgets.dropdown("make_plots", "true", ["true", "false"], "Make plots?")
dbutils.widgets.dropdown("write_tables", "true", ["true", "false"], "Write Delta tables?")
dbutils.widgets.text("output_catalog", "waterkeringbeheer_prd", "Output catalog")
dbutils.widgets.text("output_schema", "jvanruitenbeek", "Output schema")
dbutils.widgets.text("metrics_table", "",
                     "Fit metrics, to recover the full meetreeks_id (blank = from data_basis)")
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

from tarso_pipeline.evt_report import RETURN_LEVEL_COLUMNS, TAILS
from tarso_pipeline.extremes_run import discover_models, run_model
from tarso_pipeline.imprex import discover_members
from tarso_pipeline.paths import (
    BIAS_LOG_FILE,
    EVT_SELECTION_FILE,
    EXTREMES_DIRS,
    TAIL_PARAMETERS_FILE,
    extremes_dir,
    basis_volume,
    extremes_summary_file,
    output_table,
    run_dir,
)

BASIS = dbutils.widgets.get("data_basis")
VOLUME_BASE = dbutils.widgets.get("volume_base").strip().rstrip("/")
# The fit stage's run folders, per data basis. The IMPREX forcing is shared.
RUN_DIR = run_dir(basis_volume(VOLUME_BASE, BASIS), name=dbutils.widgets.get("run_folder").strip() or None)
IMPREX_DIR = dbutils.widgets.get("imprex_dir").strip() or f"{VOLUME_BASE}/knmi_forcing"
LOCATION = dbutils.widgets.get("location").strip()

UNITS = dbutils.widgets.get("forcing_units").strip()
WARMUP_YEARS = int(dbutils.widgets.get("warmup_years"))
EXCLUDE_FIRST = int(dbutils.widgets.get("exclude_first"))
T_LIST = tuple(int(t) for t in dbutils.widgets.get("t_list").split())
LIMIT_MODELS = int(dbutils.widgets.get("limit_models"))
MODEL_TYPES = [m.strip() for m in dbutils.widgets.get("model_types").split(",") if m.strip()] or None
SELECT_MODELS = dbutils.widgets.get("select_models") == "true"

KEPT_GRID = tuple(int(k) for k in dbutils.widgets.get("kept_grid").split())
MAKE_PLOTS = dbutils.widgets.get("make_plots") == "true"
WRITE_TABLES = dbutils.widgets.get("write_tables") == "true"
CATALOG = dbutils.widgets.get("output_catalog").strip()
SCHEMA = dbutils.widgets.get("output_schema").strip()
RUN_ID = (
    dbutils.widgets.get("run_id").strip()
    or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
)

METRICS_TABLE = (dbutils.widgets.get("metrics_table").strip()
                 or output_table(BASIS, "fit_metrics", CATALOG, SCHEMA))
SELECTION_TABLE = output_table(BASIS, "evt_selection", CATALOG, SCHEMA)
SUMMARY_TABLE = output_table(BASIS, "extremes_summary", CATALOG, SCHEMA)
RETURN_LEVELS_TABLE = output_table(BASIS, "return_levels", CATALOG, SCHEMA)
CHECKS_TABLE = output_table(BASIS, "model_checks", CATALOG, SCHEMA)
MODEL_SELECTION_TABLE = output_table(BASIS, "model_selection", CATALOG, SCHEMA)

print(f"basis         {BASIS}")
print(f"run_id        {RUN_ID}")
print(f"run folder    {RUN_DIR}")
print(f"imprex        {IMPREX_DIR}  ({LOCATION})")
print(f"units         {UNITS}   warmup {WARMUP_YEARS}y   trim {EXCLUDE_FIRST}")
print(f"T list        {T_LIST}")
print(f"threshold     default {EXCLUDE_FIRST} trimmed; scan n_kept {KEPT_GRID}")
print(f"pastas        {ps.__version__}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## The models and the ensemble

# COMMAND ----------

models = discover_models(RUN_DIR, MODEL_TYPES)
if LIMIT_MODELS:
    models = models.head(LIMIT_MODELS)
print(f"{len(models)} model(s) in {RUN_DIR}/pas_files")

# The .pas filename only carries the truncated name, so the full 64-character
# meetreeks_id is recovered from the stage-2 metrics table. Anything unmatched
# falls back to the truncated name rather than dropping the model.
if spark.catalog.tableExists(METRICS_TABLE):
    lookup = (
        spark.table(METRICS_TABLE)
        .select("series_name", "meetreeks_id")
        .distinct()
        .toPandas()
    )
    models = models.merge(lookup, on="series_name", how="left")
else:
    print(f"NOTE: {METRICS_TABLE} not found; meetreeks_id falls back to series_name")
    models["meetreeks_id"] = None

unmatched = int(models["meetreeks_id"].isna().sum())
models["meetreeks_id"] = models["meetreeks_id"].fillna(models["series_name"])
print(f"full meetreeks_id recovered for {len(models) - unmatched}/{len(models)} model(s)"
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
# The ensemble is read once, not once per model.
from tarso_pipeline.imprex import load_member  # noqa: E402

FORCING = {m.member: load_member(m, units=UNITS) for m in members}
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

summaries, selections, parameters, levels = [], [], [], []

for item in models.itertuples():
    print(f"\n{item.series_name}  {item.model_type}")
    result = run_model(
        model_path=item.model_path,
        meetreeks_id=item.meetreeks_id,
        series_name=item.series_name,
        members=members,
        run_directory=RUN_DIR,
        run_id=RUN_ID,
        units=UNITS,
        warmup_years=WARMUP_YEARS,
        exclude_first=EXCLUDE_FIRST,
        t_list=T_LIST,
        kept_grid=KEPT_GRID,
        make_plots=MAKE_PLOTS,
        forcing=FORCING,
        model_type=item.model_type,
    )
    summaries.append(result.summary)
    selections.extend(result.selection)
    parameters.extend(result.parameters)
    levels.extend(result.levels)

    s = result.summary
    print(f"   status={s['status']}  members={s['n_members']}  "
          f"dry={s['n_dry']} wet={s['n_wet']}  years {s['first_year']}..{s['last_year']}")
    for prefix, tail in (("dr", "dry"), ("wet", "wet")):
        shape = s.get(f"{prefix}_gpd_shape")
        print(f"   {tail:4} GPD shape={'-' if shape is None else f'{shape:+.3f}'}  "
              f"emp T100={s.get(f'{prefix}_emp_T100')}  "
              f"T1000 gpd={s.get(f'{prefix}_gpd_T1000')} exp={s.get(f'{prefix}_exp_T1000')}"
              + (f"  [{s[f'{prefix}_flag']}]" if s.get(f"{prefix}_flag") else ""))
    if s["failure_detail"]:
        print(f"   failures: {s['failure_detail'][:160]}")

summary_frame = pd.DataFrame(summaries)
selection_frame = pd.DataFrame(selections)
parameter_frame = pd.DataFrame(parameters)
levels_frame = pd.DataFrame(levels, columns=RETURN_LEVEL_COLUMNS)
print(f"\n{len(summary_frame)} model(s): "
      f"{summary_frame['status'].value_counts().to_dict()}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Run-level roll-ups
# MAGIC
# MAGIC `evt_selection` (name kept for continuity) holds per tail the diagnostics: a flag when
# MAGIC a tail is not fit to read (too few years, a capped head, a very heavy tail), the
# MAGIC threshold and the shapes. It chooses nothing.

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
        ["series_name", "model_type", "status", "n_members", "n_dry", "n_wet", "first_year", "last_year",
         "dr_flag", "dr_gpd_shape", "dr_emp_T100", "dr_gpd_T1000", "dr_exp_T1000",
         "wet_flag", "wet_gpd_shape", "wet_emp_T100", "wet_gpd_T1000", "wet_exp_T1000", "review_flag"]
    ]
)

# COMMAND ----------

# MAGIC %md
# MAGIC ## Delta tables
# MAGIC
# MAGIC Appended with the run id, so runs accumulate and join to `<prefix>_fit_metrics`
# MAGIC on `series_name`.

# COMMAND ----------

if not WRITE_TABLES:
    print("write_tables=false; nothing written")
else:
    spark.sql(f"CREATE SCHEMA IF NOT EXISTS {CATALOG}.{SCHEMA}")
    for frame, table in ((selection_frame, SELECTION_TABLE),
                         (summary_frame, SUMMARY_TABLE),
                         (levels_frame, RETURN_LEVELS_TABLE)):
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
# MAGIC ## Which model per series
# MAGIC
# MAGIC The latest fit of every model type of these series (`<prefix>_fit_metrics`,
# MAGIC `<prefix>_model_checks`) with the extremes of this run. `selected` marks the model a
# MAGIC series uses unless a person picked another in the app.

# COMMAND ----------

from tarso_pipeline.results import latest_run_sql  # noqa: E402
from tarso_pipeline.selection import select_models  # noqa: E402
from tarso_pipeline.selection import spark_schema as selection_schema  # noqa: E402

selection_models = pd.DataFrame()
if not SELECT_MODELS:
    print("select_models=false; no model selection written")
elif not spark.catalog.tableExists(METRICS_TABLE):
    print(f"no {METRICS_TABLE}; no model selection")
else:
    ids = sorted(set(models["meetreeks_id"]))
    quoted_ids = ", ".join("'" + i.replace("'", "") + "'" for i in ids)
    fit_rows = spark.sql(f"SELECT * FROM ({latest_run_sql(METRICS_TABLE)}) "
                         f"WHERE meetreeks_id IN ({quoted_ids})").toPandas()
    check_rows = (spark.sql(f"SELECT * FROM ({latest_run_sql(CHECKS_TABLE)}) "
                            f"WHERE meetreeks_id IN ({quoted_ids})").toPandas()
                  if spark.catalog.tableExists(CHECKS_TABLE) else pd.DataFrame())
    selection_models = select_models(fit_rows, check_rows, summary_frame, run_id=RUN_ID)
    chosen = selection_models[selection_models["selected"]]
    print(f"{len(chosen)} series: {chosen['model_type'].value_counts().to_dict()}; "
          f"passed {int(chosen['passed'].sum())}, runaway models {int(selection_models['runaway'].sum())}")
    display(selection_models[["series_name", "model_type", "n_param", "r2", "r2_holdout", "aic", "d_aic",
                              "d_aic_tarso", "passed", "pass_runs", "runaway", "selected", "reason"]])
    if WRITE_TABLES and not selection_models.empty:
        (spark.createDataFrame(selection_models.astype(object).where(pd.notna(selection_models), None),
                               schema=selection_schema())
         .write.format("delta").mode("append").option("mergeSchema", "true").saveAsTable(MODEL_SELECTION_TABLE))
        print(f"appended {len(selection_models)} rows to {MODEL_SELECTION_TABLE}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## What to quote
# MAGIC
# MAGIC **T <= 100: the empirical level** of the pooled years. **T = 250-500: the picked
# MAGIC family** at the picked threshold. **T=1000 is extrapolation regardless** - 704 years
# MAGIC reach an empirical return period of ~705 - so treat it as indicative, and remember the
# MAGIC Tarso model's own uncertainty is in none of these numbers.
# MAGIC
# MAGIC A `degenerate` flag on a wet tail means the model's `dmax` ceiling is capping the head,
# MAGIC not that the fit failed.

# COMMAND ----------

flagged = selection_frame[selection_frame["review_flag"].astype(bool)] if not selection_frame.empty else selection_frame
if flagged.empty:
    print("no tails flagged")
else:
    print(f"{len(flagged)} tail(s) flagged:")
    for row in flagged.itertuples():
        print(f"  {row.series_name} [{row.tail}] {row.flag}: {row.reason}")
