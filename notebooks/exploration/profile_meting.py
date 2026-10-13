# Databricks notebook source
# MAGIC %md
# MAGIC # Profile the `meting` head-measurement table
# MAGIC
# MAGIC A first look before any training: **how many series are there, and how many of
# MAGIC them span more than a year?** That second number is the one that matters — a
# MAGIC Tarso fit needs a full seasonal cycle, so it is the realistic size of the
# MAGIC training job.
# MAGIC
# MAGIC ### `id` is not the series
# MAGIC
# MAGIC `id` identifies one *measurement* (a hash per row). The **series** is
# MAGIC `meetreeks_id` — a "meetreeks" is a measurement series, and that is what becomes
# MAGIC one Pastas model. Both are counted below, because "how many unique ids" has two
# MAGIC very different answers.
# MAGIC
# MAGIC ### Two spans, deliberately
# MAGIC
# MAGIC Every span is reported twice: over **all rows**, and over **rows with a non-null
# MAGIC `waarde`**. In the sample rows that prompted this, `waarde` was null throughout —
# MAGIC if that holds widely, a series can span years of timestamps while carrying no
# MAGIC measurements at all, and only the second number is trainable.
# MAGIC
# MAGIC Read-only: this notebook writes nothing.

# COMMAND ----------

dbutils.widgets.text("catalog", "hl_grondwaterpeilmeting", "Catalog")
dbutils.widgets.text("schema", "default", "Schema")
dbutils.widgets.text("table", "meting", "Table")
dbutils.widgets.text("min_days", "365", "Minimum span in days")

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
    overview_sql,
    per_series_sql,
    qualify,
    span_buckets_sql,
    span_summary_sql,
    status_sql,
)

TABLE = qualify(
    dbutils.widgets.get("catalog"),
    dbutils.widgets.get("schema"),
    dbutils.widgets.get("table"),
)
MIN_DAYS = int(dbutils.widgets.get("min_days"))

print(f"table     {TABLE}")
print(f"min_days  {MIN_DAYS}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## What the table looks like

# COMMAND ----------

display(spark.sql(f"DESCRIBE {TABLE}"))

# COMMAND ----------

# MAGIC %md
# MAGIC ## 1. How much data, and how much of it has values

# COMMAND ----------

overview = spark.sql(overview_sql(TABLE)).toPandas()
display(overview)

row = overview.iloc[0]
print(f"rows            {row['rows_total']:,}")
print(f"unique id       {row['unique_row_ids']:,}   (one per measurement)")
print(f"unique series   {row['unique_series']:,}   (meetreeks_id - one per model)")
print(f"with a value    {row['rows_with_value']:,}  ({row['pct_with_value']}%)")
print(f"without         {row['rows_without_value']:,}")

if row["rows_with_value"] == 0:
    print("\nWARNING: every waarde is NULL. There is nothing to fit - check whether "
          "the values live in another column or another table.")
elif row["pct_with_value"] < 50:
    print(f"\nNOTE: {100 - row['pct_with_value']:.1f}% of rows carry no value. The "
          "'values only' counts below are the ones that matter.")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 2. Series spanning more than a year
# MAGIC
# MAGIC The headline number. `all rows` counts the span of bare timestamps;
# MAGIC `values only` counts the span of actual measurements — the trainable one.

# COMMAND ----------

import pandas as pd

spans = pd.concat(
    [
        spark.sql(span_summary_sql(TABLE, MIN_DAYS, values_only=False))
        .toPandas()
        .assign(basis="all rows"),
        spark.sql(span_summary_sql(TABLE, MIN_DAYS, values_only=True))
        .toPandas()
        .assign(basis="values only"),
    ],
    ignore_index=True,
)
display(spans[["basis", "series_total", "series_long_enough", "series_too_short",
               "pct_long_enough", "median_span_days", "max_span_days"]])

for _, s in spans.iterrows():
    print(f"{s['basis']:<12} {s['series_long_enough']:,} of {s['series_total']:,} series "
          f"span >= {MIN_DAYS} days ({s['pct_long_enough']}%)")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 3. The shape of it
# MAGIC
# MAGIC One number hides the distribution: a table that is mostly 11-month series is a
# MAGIC very different proposition from one that is mostly 10-year series.

# COMMAND ----------

display(spark.sql(span_buckets_sql(TABLE, values_only=True)))

# COMMAND ----------

# MAGIC %md
# MAGIC ## 4. Validation status
# MAGIC
# MAGIC What the verdict column contains, and whether it explains the missing values.

# COMMAND ----------

display(spark.sql(status_sql(TABLE)))

# COMMAND ----------

# MAGIC %md
# MAGIC ## 5. The longest series
# MAGIC
# MAGIC Useful as training candidates to try first.

# COMMAND ----------

display(
    spark.sql(
        f"""
        WITH per_series AS ({per_series_sql(TABLE, values_only=True)})
        SELECT * FROM per_series
        WHERE span_days >= {MIN_DAYS}
        ORDER BY span_days DESC, n_values DESC
        LIMIT 25
        """
    )
)
