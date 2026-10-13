# Databricks notebook source
# MAGIC %md
# MAGIC # Which series are fit to model?
# MAGIC
# MAGIC Step 1 of the training job. Reads `meting`, classifies every series, and reports
# MAGIC **how many can actually be modelled**. Optionally writes that list to a table for
# MAGIC the training task to consume.
# MAGIC
# MAGIC ### The verdicts
# MAGIC
# MAGIC Mutually exclusive and ordered, so the counts add up to the total and each series
# MAGIC is reported by its most fundamental problem:
# MAGIC
# MAGIC | verdict | meaning |
# MAGIC |---|---|
# MAGIC | `trainable` | enough measurements, spread over enough time |
# MAGIC | `no values` | rows exist but every `waarde` is NULL — nothing to fit |
# MAGIC | `too few values` | fewer than `min_obs` actual measurements |
# MAGIC | `too short` | measurements span less than `min_days` |
# MAGIC
# MAGIC ### Span is measured over *measurements*, not timestamps
# MAGIC
# MAGIC From the first row with a `waarde` to the last. A series carrying two years of
# MAGIC empty rows is not two years of data — and in the sample rows that prompted this,
# MAGIC `waarde` was NULL throughout.
# MAGIC
# MAGIC Both filters matter: a series can span three years and still hold 40 readings,
# MAGIC which is a span that looks fine and a fit that is not.
# MAGIC
# MAGIC Read-only unless `write_output` is turned on.

# COMMAND ----------

dbutils.widgets.text("catalog", "gold_prd", "Source catalog")
dbutils.widgets.text("schema", "hl_grondwaterpeilmeting", "Source schema")
dbutils.widgets.text("table", "meting", "Source table")

dbutils.widgets.text("min_days", "365", "Minimum span in days")
dbutils.widgets.text("min_obs", "100", "Minimum measurements")

dbutils.widgets.dropdown("write_output", "false", ["false", "true"], "Write the list?")
dbutils.widgets.text("output_catalog", "", "Output catalog (write only)")
dbutils.widgets.text("output_schema", "", "Output schema (write only)")
dbutils.widgets.text("output_table", "trainable_series", "Output table (write only)")

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

from tarso_pipeline.meting import (
    TRAINABLE,
    qualify,
    series_verdict_sql,
    trainable_series_sql,
    verdict_summary_sql,
)

SOURCE = qualify(
    dbutils.widgets.get("catalog"),
    dbutils.widgets.get("schema"),
    dbutils.widgets.get("table"),
)
MIN_DAYS = int(dbutils.widgets.get("min_days"))
MIN_OBS = int(dbutils.widgets.get("min_obs"))
WRITE_OUTPUT = dbutils.widgets.get("write_output") == "true"

print(f"source    {SOURCE}")
print(f"criteria  at least {MIN_OBS} measurements, spanning at least {MIN_DAYS} days")

# COMMAND ----------

# MAGIC %md
# MAGIC ## The answer

# COMMAND ----------

summary = spark.sql(verdict_summary_sql(SOURCE, MIN_DAYS, MIN_OBS)).toPandas()
display(summary)

total = int(summary["series"].sum())
trainable_row = summary[summary["verdict"] == TRAINABLE]
trainable = int(trainable_row["series"].iloc[0]) if not trainable_row.empty else 0

print(f"{trainable:,} of {total:,} series are fit to model "
      f"({100 * trainable / total:.1f}%)" if total else "no series found")

for row in summary.itertuples():
    if row.verdict != TRAINABLE:
        print(f"  excluded, {row.verdict}: {row.series:,}")

if trainable == 0:
    print("\nNothing is trainable. Check the 'no values' count first: if it is the "
          "whole table, the head values are not in `waarde` and the column needs "
          "revisiting before anything else.")

# COMMAND ----------

# MAGIC %md
# MAGIC ## The series themselves
# MAGIC
# MAGIC Longest first — the top of this list is where to start when you run the first
# MAGIC real fits.

# COMMAND ----------

candidates = spark.sql(trainable_series_sql(SOURCE, MIN_DAYS, MIN_OBS))
display(candidates.limit(100))

# COMMAND ----------

# MAGIC %md
# MAGIC ## Everything, with its verdict
# MAGIC
# MAGIC Including the rejects — a series that is excluded should be visible, not
# MAGIC silently absent.

# COMMAND ----------

display(
    spark.sql(
        f"""
        WITH scored AS ({series_verdict_sql(SOURCE, MIN_DAYS, MIN_OBS)})
        SELECT * FROM scored ORDER BY verdict, span_days DESC
        """
    ).limit(500)
)

# COMMAND ----------

# MAGIC %md
# MAGIC ## Optionally hand the list on
# MAGIC
# MAGIC Turn on `write_output` to persist the candidates, so a training task can read
# MAGIC this table instead of re-deciding the criteria for itself. Overwritten each run:
# MAGIC it is a current view of what is trainable, not a history.

# COMMAND ----------

if not WRITE_OUTPUT:
    print("write_output=false; nothing written")
else:
    OUTPUT = qualify(
        dbutils.widgets.get("output_catalog") or dbutils.widgets.get("catalog"),
        dbutils.widgets.get("output_schema") or dbutils.widgets.get("schema"),
        dbutils.widgets.get("output_table"),
    )
    spark.sql(f"CREATE SCHEMA IF NOT EXISTS {OUTPUT.rsplit('.', 1)[0]}")
    (
        candidates.withColumn("min_days", F.lit(MIN_DAYS))
        .withColumn("min_obs", F.lit(MIN_OBS))
        .withColumn("source_table", F.lit(SOURCE))
        .withColumn("found_at", F.current_timestamp())
        .write.format("delta")
        .mode("overwrite")
        .option("overwriteSchema", "true")
        .saveAsTable(OUTPUT)
    )
    print(f"wrote {candidates.count():,} candidate series to {OUTPUT}")
