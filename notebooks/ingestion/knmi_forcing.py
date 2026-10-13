# Databricks notebook source
# MAGIC %md
# MAGIC # KNMI daily forcing: precipitation and Makkink evaporation
# MAGIC
# MAGIC Pulls daily **precipitation (RH)** and **Makkink reference evaporation (EV24)**
# MAGIC from the KNMI open API into a Delta table, then charts annual totals as a check.
# MAGIC
# MAGIC ### Why the *daily* endpoint, not the hourly one
# MAGIC
# MAGIC The hourly product (`uurgegevens`) has no Makkink evaporation: its `PRCP` set is
# MAGIC `DR:RH`, precipitation duration and the hourly sum. **`EV24` exists only in the
# MAGIC daily product** (`daggegevens`), so that is what this notebook calls. Daily is also
# MAGIC what the pipeline wants anyway - `DATA_CATALOG.md` section 3 specifies forcings as
# MAGIC millimetres **per day**.
# MAGIC
# MAGIC ### Units
# MAGIC
# MAGIC KNMI publishes both variables in 0.1 mm; they are converted to mm on the way in.
# MAGIC `RH = -1` is KNMI's code for "less than 0.05 mm" and is read as `0.0` - a dry day,
# MAGIC not a missing one. Blank fields stay `NULL`.
# MAGIC
# MAGIC ### Only fetching what is missing
# MAGIC
# MAGIC The target table is the watermark. Each run reads its newest stored date and asks
# MAGIC KNMI only for what comes after, so the first run pulls the 2020-to-today backlog
# MAGIC and every later run pulls a handful of days. When the table is already current the
# MAGIC notebook **sends no request at all**.
# MAGIC
# MAGIC The window reaches back `lookback_days` before the watermark, because KNMI revises
# MAGIC the most recent days as values are validated. Rows are written with a `MERGE` on
# MAGIC `(station, date)`, so re-fetching the same dates corrects them instead of
# MAGIC duplicating them.
# MAGIC
# MAGIC > KNMI publishes about one to two days behind, so "today" normally returns data up
# MAGIC > to the day before yesterday. That is expected, not a failure.

# COMMAND ----------

# MAGIC %md
# MAGIC ## Parameters

# COMMAND ----------

dbutils.widgets.text("catalog", "dev_catalog", "Catalog")
dbutils.widgets.text("schema", "default", "Schema")
dbutils.widgets.text("table", "knmi_daily_forcing", "Table")
dbutils.widgets.text("station", "249", "KNMI station")
dbutils.widgets.text("backlog_start", "2020-01-01", "Backlog start (first run)")
dbutils.widgets.text("lookback_days", "2", "Re-fetch days before watermark")

# "api"  call KNMI directly. Needs outbound HTTPS from the cluster.
# "file" read a CSV exported by scripts/check_knmi.py --export, for a cluster
#        with no internet egress. Everything downstream is identical.
dbutils.widgets.dropdown("source", "api", ["api", "file"], "Source")
dbutils.widgets.text("source_path", "", "Export CSV path (source=file)")

# COMMAND ----------

import datetime as dt
import os
import sys

# The package lives in <repo>/src. Walk up from the working directory for the
# pyproject rather than assuming a fixed depth, so this survives being run as a
# job task (where the working directory is not always the notebook's folder).
# Alternative: %pip install -e <repo> , which needs a Python restart.
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
print(f"importing tarso_pipeline from {SRC}")

import pandas as pd
from pyspark.sql.types import (
    DateType,
    DoubleType,
    IntegerType,
    StructField,
    StructType,
    TimestampType,
)

from tarso_pipeline.forcing_plot import (
    annual_totals,
    plot_annual_totals,
    use_headless_backend,
)
from tarso_pipeline.knmi import fetch_daily, plan_window, read_export

CATALOG = dbutils.widgets.get("catalog")
SCHEMA = dbutils.widgets.get("schema")
TABLE = f"{CATALOG}.{SCHEMA}.{dbutils.widgets.get('table')}"
STATION = int(dbutils.widgets.get("station"))
BACKLOG_START = dt.date.fromisoformat(dbutils.widgets.get("backlog_start"))
LOOKBACK_DAYS = int(dbutils.widgets.get("lookback_days"))
SOURCE = dbutils.widgets.get("source")
SOURCE_PATH = dbutils.widgets.get("source_path").strip()
TODAY = dt.date.today()

if SOURCE == "file" and not SOURCE_PATH:
    raise ValueError("source=file needs source_path, e.g. /Workspace/Users/<you>/knmi.csv")

print(f"target      {TABLE}")
print(f"station     {STATION}")
print(f"backlog     {BACKLOG_START} -> {TODAY}")
print(f"source      {SOURCE}{' ' + SOURCE_PATH if SOURCE == 'file' else ''}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Target table

# COMMAND ----------

spark.sql(f"CREATE SCHEMA IF NOT EXISTS {CATALOG}.{SCHEMA}")
spark.sql(
    f"""
    CREATE TABLE IF NOT EXISTS {TABLE} (
        station          INT       COMMENT 'KNMI station number',
        date             DATE      COMMENT 'Observation day',
        precipitation_mm DOUBLE    COMMENT 'Daily precipitation sum, mm (KNMI RH / 10)',
        makkink_mm       DOUBLE    COMMENT 'Makkink reference evaporation, mm (KNMI EV24 / 10)',
        ingested_at      TIMESTAMP COMMENT 'When this row was last written'
    )
    USING DELTA
    COMMENT 'Daily KNMI forcing in mm. Natural key (station, date); MERGE-upserted.'
    """
)
print(f"{TABLE} ready")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Work out what is missing

# COMMAND ----------

watermark_row = spark.sql(
    f"SELECT max(date) AS last_date FROM {TABLE} WHERE station = {STATION}"
).collect()[0]
last_stored = watermark_row["last_date"]  # None when the table is empty

window = plan_window(
    last_stored=last_stored,
    today=TODAY,
    backlog_start=BACKLOG_START,
    lookback_days=LOOKBACK_DAYS,
)

if last_stored is None:
    print(f"no data stored yet -> fetching the backlog {window.start} -> {window.end}")
elif window.is_empty:
    print(f"already current through {last_stored}; nothing to fetch")
else:
    print(f"stored through {last_stored} -> fetching {window.start} -> {window.end}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Fetch and merge
# MAGIC
# MAGIC `source=api` calls KNMI; an empty window short-circuits inside `fetch_daily`, so
# MAGIC no HTTP request is made.
# MAGIC
# MAGIC `source=file` reads a CSV exported by `scripts/check_knmi.py --export` instead —
# MAGIC for a cluster with no outbound internet. The frame is the same shape either way,
# MAGIC so the `MERGE` below does not know or care which produced it. Re-merging rows
# MAGIC that are already stored is harmless: the key is `(station, date)`.

# COMMAND ----------

if SOURCE == "file":
    fetched = read_export(SOURCE_PATH, station=STATION)
    print(f"{len(fetched)} rows from {SOURCE_PATH}")
    if not fetched.empty:
        print(f"  covering {fetched['date'].min().date()} -> {fetched['date'].max().date()}")
        stale_by = (TODAY - fetched["date"].max().date()).days
        if stale_by > 3:
            # KNMI itself runs 1-2 days behind, so a few days is normal; more than
            # that means the export needs regenerating on a machine with egress.
            print(f"  NOTE: export ends {stale_by} days ago; re-run check_knmi.py --export")
else:
    fetched = fetch_daily(window, station=STATION)
    print(f"{len(fetched)} rows from KNMI")

SCHEMA_SPARK = StructType(
    [
        StructField("station", IntegerType(), False),
        StructField("date", DateType(), False),
        StructField("precipitation_mm", DoubleType(), True),
        StructField("makkink_mm", DoubleType(), True),
        StructField("ingested_at", TimestampType(), False),
    ]
)


def to_rows(pdf: pd.DataFrame, now: dt.datetime) -> list[tuple]:
    """pandas -> plain tuples, turning NaN into None so Delta stores real NULLs."""
    rows = []
    for record in pdf.itertuples(index=False):
        rows.append(
            (
                int(record.station),
                record.date.date(),
                None if pd.isna(record.precipitation_mm) else float(record.precipitation_mm),
                None if pd.isna(record.makkink_mm) else float(record.makkink_mm),
                now,
            )
        )
    return rows


if fetched.empty:
    print("nothing to merge")
else:
    updates = spark.createDataFrame(to_rows(fetched, dt.datetime.now()), schema=SCHEMA_SPARK)
    updates.createOrReplaceTempView("knmi_updates")
    spark.sql(
        f"""
        MERGE INTO {TABLE} AS t
        USING knmi_updates AS s
          ON t.station = s.station AND t.date = s.date
        WHEN MATCHED THEN UPDATE SET *
        WHEN NOT MATCHED THEN INSERT *
        """
    )
    print(f"merged {updates.count()} rows into {TABLE}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Coverage check
# MAGIC
# MAGIC Days present vs. days expected. A gap here means a genuinely missing KNMI day,
# MAGIC which matters: Pastas wants an unbroken daily forcing series.

# COMMAND ----------

coverage = spark.sql(
    f"""
    SELECT
        min(date)                                        AS first_date,
        max(date)                                        AS last_date,
        count(*)                                         AS days_stored,
        datediff(max(date), min(date)) + 1                AS days_expected,
        count(*) - (datediff(max(date), min(date)) + 1)   AS gap,
        sum(CASE WHEN precipitation_mm IS NULL THEN 1 ELSE 0 END) AS null_precipitation,
        sum(CASE WHEN makkink_mm       IS NULL THEN 1 ELSE 0 END) AS null_makkink
    FROM {TABLE}
    WHERE station = {STATION}
    """
).toPandas()

display(coverage)

gap = int(coverage.loc[0, "gap"])
if gap != 0:
    print(f"WARNING: {abs(gap)} day(s) missing between {coverage.loc[0, 'first_date']} "
          f"and {coverage.loc[0, 'last_date']}")
else:
    print("no missing days")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Annual totals
# MAGIC
# MAGIC Each year's cumulative precipitation and evaporation, side by side. Both are mm,
# MAGIC so they share one axis and are directly comparable: precipitation above
# MAGIC evaporation is a recharge surplus for that year.

# COMMAND ----------

daily = spark.sql(
    f"SELECT date, precipitation_mm, makkink_mm FROM {TABLE} "
    f"WHERE station = {STATION} AND date >= '{BACKLOG_START}' ORDER BY date"
).toPandas()
daily["date"] = pd.to_datetime(daily["date"])


totals = annual_totals(daily)
display(totals.reset_index())

# COMMAND ----------

use_headless_backend()  # no interactive display on a cluster
fig = plot_annual_totals(totals, STATION)
display(fig)
