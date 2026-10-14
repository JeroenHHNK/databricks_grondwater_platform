# Databricks notebook source
# MAGIC %md
# MAGIC # Build the series manifest
# MAGIC
# MAGIC Task 1 of the pipeline. Picks the **20 longest trainable series** from
# MAGIC `gold_prd.hl_grondwaterpeilmeting.meting` and writes them to
# MAGIC `waterkeringbeheer_prd.jvanruitenbeek.pastas_series_manifest`, then quantifies the
# MAGIC duplicate-timestamp problem before anything is fitted.
# MAGIC
# MAGIC ### Permissions
# MAGIC
# MAGIC `gold_prd` is **read-only** — `SELECT`/`EXECUTE`/`READ VOLUME`, no `CREATE`. All
# MAGIC output goes to `waterkeringbeheer_prd.jvanruitenbeek`, which was confirmed with a
# MAGIC full create → insert → read → drop lifecycle.
# MAGIC
# MAGIC Note `spark.catalog.currentCatalog()` returns `dbw_hhnkda_prd_05` — that is the
# MAGIC workspace/metastore name, **not** a catalog. Everything here uses explicit
# MAGIC three-part names rooted at a real catalog.
# MAGIC
# MAGIC ### Why the duplicate check comes before fitting
# MAGIC
# MAGIC The longest series carry ~55 values per day, far denser than hourly. Pastas wants
# MAGIC a regular, deduplicated series; duplicates give either a failure or a silently
# MAGIC meaningless fit. The check below separates the two possible causes:
# MAGIC **duplicate timestamps** (`n_duplicate_ts` high) from **genuine sub-hourly
# MAGIC sampling** (`n_duplicate_ts` zero, `distinct_ts_per_day` high). They need
# MAGIC different cleaning rules.

# COMMAND ----------

dbutils.widgets.text("source_table", "gold_prd.hl_grondwaterpeilmeting.meting", "Source")
dbutils.widgets.text("manifest_table",
                     "waterkeringbeheer_prd.jvanruitenbeek.pastas_series_manifest",
                     "Manifest table")
dbutils.widgets.text("limit", "20", "How many series")
dbutils.widgets.text("min_days", "365", "Minimum span in days")
dbutils.widgets.text("min_obs", "100", "Minimum measurements")
dbutils.widgets.text("run_id", "", "Run id (blank = timestamp)")
dbutils.widgets.dropdown("write_manifest", "true", ["true", "false"], "Write the manifest?")

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

from tarso_pipeline.fit_metrics import make_run_id
from tarso_pipeline.meting import (
    duplicate_timestamps_sql,
    manifest_sql,
    verdict_summary_sql,
)

SOURCE = dbutils.widgets.get("source_table").strip()
MANIFEST = dbutils.widgets.get("manifest_table").strip()
LIMIT = int(dbutils.widgets.get("limit"))
MIN_DAYS = int(dbutils.widgets.get("min_days"))
MIN_OBS = int(dbutils.widgets.get("min_obs"))
RUN_ID = dbutils.widgets.get("run_id").strip() or make_run_id()
WRITE_MANIFEST = dbutils.widgets.get("write_manifest") == "true"

print(f"source    {SOURCE}")
print(f"manifest  {MANIFEST}")
print(f"run_id    {RUN_ID}")
print(f"selecting the {LIMIT} longest series with >= {MIN_OBS} values over >= {MIN_DAYS} days")

# COMMAND ----------

# MAGIC %md
# MAGIC ## What the table actually contains
# MAGIC
# MAGIC Open question 1 from the handover: confirm the timestamp and value column names
# MAGIC before trusting anything below. The code assumes `tijdstempel` and `waarde`.

# COMMAND ----------

display(spark.sql(f"DESCRIBE {SOURCE}"))

# COMMAND ----------

display(spark.sql(f"SELECT * FROM {SOURCE} LIMIT 5"))

# COMMAND ----------

# MAGIC %md
# MAGIC ## How many series are fit to model

# COMMAND ----------

display(spark.sql(verdict_summary_sql(SOURCE, MIN_DAYS, MIN_OBS)))

# COMMAND ----------

# MAGIC %md
# MAGIC ## The manifest: the longest series

# COMMAND ----------

manifest = (
    spark.sql(manifest_sql(SOURCE, MIN_DAYS, MIN_OBS, LIMIT))
    .withColumn("run_id", F.lit(RUN_ID))
    .withColumn("min_days", F.lit(MIN_DAYS))
    .withColumn("min_obs", F.lit(MIN_OBS))
    .withColumn("source_table", F.lit(SOURCE))
    .withColumn("manifest_created_at", F.current_timestamp())
)
manifest.cache()
print(f"{manifest.count()} series selected")
display(manifest)

# COMMAND ----------

if not WRITE_MANIFEST:
    print("write_manifest=false; nothing written")
else:
    manifest.write.format("delta").mode("overwrite").option(
        "overwriteSchema", "true"
    ).saveAsTable(MANIFEST)
    print(f"wrote {manifest.count()} rows to {MANIFEST}")
    display(spark.table(MANIFEST))

# COMMAND ----------

# MAGIC %md
# MAGIC ## Duplicate timestamps (handover section 4)
# MAGIC
# MAGIC Resolve this before fitting. Read it as:
# MAGIC
# MAGIC - **`n_duplicate_ts` large** → the same instant is recorded many times.
# MAGIC   Collapse them (`dedup_method`, default `mean`) before resampling.
# MAGIC - **`n_duplicate_ts` ~0 but `distinct_ts_per_day` large** → genuine sub-hourly
# MAGIC   sampling. No duplicates to remove; the daily resample does the work.
# MAGIC
# MAGIC `clean_head` handles both, and records the counts on every metrics row so two
# MAGIC fits stay comparable.

# COMMAND ----------

if not WRITE_MANIFEST:
    print("manifest not written; skipping the duplicate check (it joins to the table)")
else:
    duplicates = spark.sql(duplicate_timestamps_sql(SOURCE, MANIFEST)).toPandas()
    display(duplicates)

    if duplicates.empty:
        print("no rows returned - does the manifest hold any series?")
    else:
        worst = duplicates.iloc[0]
        total_dup = int(duplicates["n_duplicate_ts"].sum())
        print(f"duplicate timestamps across the manifest: {total_dup:,}")
        print(f"worst series {worst['series_id'][:12]}...: "
              f"{worst['n_duplicate_ts']:,} duplicates "
              f"({worst['pct_duplicate']}% of its rows), "
              f"{worst['rows_per_day']} rows/day vs "
              f"{worst['distinct_ts_per_day']} distinct timestamps/day")
        if total_dup == 0:
            print("\n-> No duplicates. The density is real sub-hourly sampling; "
                  "the daily resample alone is the right cleaning rule.")
        else:
            print("\n-> Duplicates confirmed. dedup_method collapses them before "
                  "resampling; 'mean' is the default and is recorded per fit.")
