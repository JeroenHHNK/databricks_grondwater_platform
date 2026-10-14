# Databricks notebook source
# MAGIC %md
# MAGIC # Inspect the fitted models
# MAGIC
# MAGIC **Run by hand, not as a job task.** Read-only. Reads
# MAGIC `pastas_fit_metrics` and answers the questions you have after a run:
# MAGIC
# MAGIC - did it work, and where did it fail?
# MAGIC - are the fits any good?
# MAGIC - did Tarso beat the plain recharge baseline?
# MAGIC - did the cleaning do what we think?
# MAGIC - can a saved `.pas` be loaded back?
# MAGIC
# MAGIC ### Reading the fit statistics
# MAGIC
# MAGIC `EVP` is explained variance as a percentage and `R2` the same thing as a
# MAGIC fraction. **Healthy is roughly 0.7–0.95 R2.** Below ~0.5 the model is not
# MAGIC describing the series, even though the fit "succeeded" — which is why this
# MAGIC notebook separates *succeeded* from *is any good*.

# COMMAND ----------

dbutils.widgets.text("metrics_table",
                     "waterkeringbeheer_prd.jvanruitenbeek.pastas_fit_metrics",
                     "Metrics table")
dbutils.widgets.text("manifest_table",
                     "waterkeringbeheer_prd.jvanruitenbeek.pastas_series_manifest",
                     "Manifest table")
dbutils.widgets.text("run_id", "", "Run id (blank = the latest)")
dbutils.widgets.text("good_r2", "0.7", "R2 considered healthy")

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

from pyspark.sql import functions as F

METRICS = dbutils.widgets.get("metrics_table").strip()
MANIFEST = dbutils.widgets.get("manifest_table").strip()
GOOD_R2 = float(dbutils.widgets.get("good_r2"))

requested_run = dbutils.widgets.get("run_id").strip()
runs = (
    spark.table(METRICS)
    .groupBy("run_id")
    .agg(
        F.count("*").alias("fits"),
        F.sum(F.col("success").cast("int")).alias("succeeded"),
        F.max("fitted_at").alias("finished_at"),
    )
    .orderBy(F.col("finished_at").desc())
)
display(runs)

RUN_ID = requested_run or runs.first()["run_id"]
print(f"inspecting run {RUN_ID}")

# COMMAND ----------

metrics = spark.table(METRICS).where(F.col("run_id") == RUN_ID)
metrics.cache()

# COMMAND ----------

# MAGIC %md
# MAGIC ## 1. Did it work?

# COMMAND ----------

display(
    metrics.groupBy("model_type", "success")
    .agg(
        F.count("*").alias("fits"),
        F.round(F.avg("r2"), 3).alias("mean_r2"),
        F.round(F.avg("evp"), 1).alias("mean_evp"),
    )
    .orderBy("model_type", "success")
)

# COMMAND ----------

# MAGIC %md
# MAGIC ## 2. What failed, and why
# MAGIC
# MAGIC Failures are rows, not crashes — so they are all here. Group by the message:
# MAGIC twenty series failing for one reason is one bug, not twenty.

# COMMAND ----------

failures = metrics.where(~F.col("success"))
if failures.count() == 0:
    print("no failures in this run")
else:
    display(
        failures.withColumn(
            "error_kind", F.split(F.col("error_message"), ":").getItem(0)
        )
        .groupBy("error_kind")
        .agg(F.count("*").alias("series"),
             F.first("error_message").alias("example"))
        .orderBy(F.col("series").desc())
    )
    display(failures.select("series_name", "model_type", "error_message",
                            "n_raw_rows", "n_after_dedup", "span_days"))

# COMMAND ----------

# MAGIC %md
# MAGIC ## 3. Are the fits any good?
# MAGIC
# MAGIC Succeeding and being useful are different things.

# COMMAND ----------

good = metrics.where(F.col("success"))
display(
    good.select("series_name", "model_type", "r2", "evp", "rmse",
                "nobs", "span_days", "aicc", "bic", "obj", "nfev")
    .orderBy(F.col("r2").desc())
)

counts = good.select(
    F.sum((F.col("r2") >= GOOD_R2).cast("int")).alias("healthy"),
    F.sum((F.col("r2") < GOOD_R2).cast("int")).alias("poor"),
    F.count("*").alias("total"),
).first()
if counts and counts["total"]:
    print(f"{counts['healthy']} of {counts['total']} fits have R2 >= {GOOD_R2}")
    if counts["poor"]:
        print(f"{counts['poor']} fitted but weak - inspect the cleaning for those "
              "before trusting them")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 4. Tarso vs the recharge baseline
# MAGIC
# MAGIC Only meaningful if both model types were run. If the baseline matches or beats
# MAGIC Tarso, the extra complexity is not earning its place on that series.

# COMMAND ----------

pivot = (
    good.groupBy("series_name")
    .pivot("model_type", ["tarso", "recharge"])
    .agg(F.round(F.first("r2"), 3))
)
if "recharge" in pivot.columns and "tarso" in pivot.columns:
    display(
        pivot.withColumn("tarso_better", F.col("tarso") > F.col("recharge"))
        .orderBy(F.col("tarso").desc())
    )
else:
    print("only one model type in this run; nothing to compare")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 5. Did the cleaning do what we think?
# MAGIC
# MAGIC `n_raw_rows` vs `n_after_dedup` shows how much of the source was duplicate
# MAGIC timestamps. A series where they are equal had no duplicates at all.

# COMMAND ----------

display(
    metrics.select(
        "series_name", "n_raw_rows", "n_missing_value", "n_duplicate_timestamps",
        "n_after_dedup", "nobs", "dedup_method", "resample_rule", "resample_method",
    )
    .withColumn(
        "pct_duplicate",
        F.round(100 * F.col("n_duplicate_timestamps") / F.col("n_raw_rows"), 1),
    )
    .orderBy(F.col("pct_duplicate").desc())
)

# COMMAND ----------

# MAGIC %md
# MAGIC ## 6. Load a saved model back
# MAGIC
# MAGIC Proves the `.pas` files are real and reusable, rather than just written.

# COMMAND ----------

saved = metrics.where(F.col("model_path").isNotNull()).select(
    "series_name", "model_type", "model_path"
).collect()

if not saved:
    print("no .pas files recorded for this run")
else:
    # %pip in a notebook restarts Python, so only install when actually needed.
    import importlib.util

    if importlib.util.find_spec("pastas") is None:
        print("pastas is not installed here; run the fit notebook to load models")
    else:
        import pastas as ps

        first = saved[0]
        model = ps.io.load(first["model_path"])
        print(f"loaded {first['series_name']} ({first['model_type']}) "
              f"from {first['model_path']}")
        print(f"  observations {model.oseries.series.size}")
        print(f"  parameters   {list(model.parameters.index)}")
        print(f"\n{len(saved)} model file(s) recorded in this run")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 7. Which manifest series never produced a fit
# MAGIC
# MAGIC A series selected for work but missing from the metrics table is a silent gap —
# MAGIC the run stopped early, or the series was never attempted.

# COMMAND ----------

attempted = metrics.select("series_id").distinct()
missing = spark.table(MANIFEST).select("series_id", "span_days", "n_values").join(
    attempted, on="series_id", how="left_anti"
)
if missing.count() == 0:
    print("every manifest series was attempted")
else:
    print(f"{missing.count()} manifest series produced no row at all:")
    display(missing)
