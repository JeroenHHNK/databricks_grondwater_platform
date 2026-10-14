# Databricks notebook source
# MAGIC %md
# MAGIC # Explore the `meting` source table
# MAGIC
# MAGIC **Run by hand, not as a job task.** Read-only. Answers "what is in there, and how
# MAGIC much of it can we model?" before the pipeline commits to anything.
# MAGIC
# MAGIC Run this when the source changes, when a fit result looks wrong, or when the
# MAGIC `trainable` count moves unexpectedly.
# MAGIC
# MAGIC ### `id` is not the series
# MAGIC
# MAGIC `id` identifies one *measurement* (a per-row hash). The **series** is
# MAGIC `meetreeks_id`, and that is what becomes one Pastas model. Both are counted,
# MAGIC because "how many unique ids" has two very different answers.
# MAGIC
# MAGIC ### Spans are measured over measurements, not timestamps
# MAGIC
# MAGIC From the first row with a `waarde` to the last. A series carrying two years of
# MAGIC empty rows is not two years of data.

# COMMAND ----------

dbutils.widgets.text("source_table", "gold_prd.hl_grondwaterpeilmeting.meting", "Source")
dbutils.widgets.text("min_days", "365", "Minimum span in days")
dbutils.widgets.text("min_obs", "100", "Minimum measurements")

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

from tarso_pipeline.meting import (
    TRAINABLE,
    overview_sql,
    series_verdict_sql,
    span_buckets_sql,
    status_sql,
    verdict_summary_sql,
)

SOURCE = dbutils.widgets.get("source_table").strip()
MIN_DAYS = int(dbutils.widgets.get("min_days"))
MIN_OBS = int(dbutils.widgets.get("min_obs"))

print(f"source    {SOURCE}")
print(f"criteria  at least {MIN_OBS} measurements over at least {MIN_DAYS} days")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 1. The schema
# MAGIC
# MAGIC Confirm the timestamp and value column names. The pipeline assumes `tijdstempel`
# MAGIC and `waarde`; if either is wrong, everything downstream is wrong.

# COMMAND ----------

display(spark.sql(f"DESCRIBE {SOURCE}"))

# COMMAND ----------

display(spark.sql(f"SELECT * FROM {SOURCE} LIMIT 5"))

# COMMAND ----------

# MAGIC %md
# MAGIC ## 2. How much data, and how much of it has values

# COMMAND ----------

overview = spark.sql(overview_sql(SOURCE)).toPandas()
display(overview)

row = overview.iloc[0]
print(f"rows            {row['rows_total']:,}")
print(f"unique id       {row['unique_row_ids']:,}   (one per measurement)")
print(f"unique series   {row['unique_series']:,}   (meetreeks_id - one per model)")
print(f"with a value    {row['rows_with_value']:,}  ({row['pct_with_value']}%)")

if row["rows_with_value"] == 0:
    print("\nWARNING: every waarde is NULL. Nothing can be fitted - check whether the "
          "values live in another column or another table.")
elif row["pct_with_value"] < 50:
    print(f"\nNOTE: {100 - row['pct_with_value']:.1f}% of rows carry no value.")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 3. How many series are fit to model
# MAGIC
# MAGIC The headline. Verdicts are mutually exclusive and ordered, so they add up to the
# MAGIC total and each series is reported by its most fundamental problem.
# MAGIC
# MAGIC | verdict | meaning |
# MAGIC |---|---|
# MAGIC | `trainable` | enough measurements, over enough time |
# MAGIC | `no values` | rows exist, every `waarde` is NULL |
# MAGIC | `too few values` | under `min_obs` measurements |
# MAGIC | `too short` | measurements span under `min_days` |

# COMMAND ----------

summary = spark.sql(verdict_summary_sql(SOURCE, MIN_DAYS, MIN_OBS)).toPandas()
display(summary)

total = int(summary["series"].sum())
hit = summary[summary["verdict"] == TRAINABLE]
trainable = int(hit["series"].iloc[0]) if not hit.empty else 0
print(f"{trainable:,} of {total:,} series are fit to model "
      f"({100 * trainable / total:.1f}%)" if total else "no series found")
for item in summary.itertuples():
    if item.verdict != TRAINABLE:
        print(f"  excluded, {item.verdict}: {item.series:,}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 4. The shape of it
# MAGIC
# MAGIC One number hides the distribution: mostly 11-month series is a very different
# MAGIC proposition from mostly 10-year series.

# COMMAND ----------

display(spark.sql(span_buckets_sql(SOURCE, values_only=True)))

# COMMAND ----------

# MAGIC %md
# MAGIC ## 5. Validation status
# MAGIC
# MAGIC What `validatie_status` contains, and whether it explains the missing values.
# MAGIC The pipeline does **not** filter on it yet — decide here whether it should.

# COMMAND ----------

display(spark.sql(status_sql(SOURCE)))

# COMMAND ----------

# MAGIC %md
# MAGIC ## 6. Every series with its verdict
# MAGIC
# MAGIC Including the rejects: an excluded series should be visible, not absent.

# COMMAND ----------

display(
    spark.sql(
        f"""
        WITH scored AS ({series_verdict_sql(SOURCE, MIN_DAYS, MIN_OBS)})
        SELECT * FROM scored ORDER BY verdict, span_days DESC
        """
    ).limit(500)
)
