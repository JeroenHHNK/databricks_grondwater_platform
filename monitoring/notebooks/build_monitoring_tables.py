# Databricks notebook source
# MAGIC %md
# MAGIC # Monitoring tables for the dashboard `Grondwater_Monitoring`
# MAGIC
# MAGIC Run **daily** as a job (see `monitoring/README.md`). It reads the source
# MAGIC (`gold_prd.hl_grondwaterpeilmeting`, `gold_prd.hl_cs_objecten`), `validated_meting`
# MAGIC and the Pastas tables, and overwrites small `monitoring_*` tables that the dashboard
# MAGIC reads, so the dashboard never scans all measurements itself.
# MAGIC
# MAGIC | table | per | what |
# MAGIC |---|---|---|
# MAGIC | `monitoring_hourly` | series x UTC hour | mean raw value (the series page plots this) |
# MAGIC | `monitoring_series_status` | series | supplier, cs_objecten match, metadata, data flow, status, validated, Pastas |
# MAGIC | `monitoring_uptime_daily` | sensor x day | hours received (24 = complete); history kept |
# MAGIC | `monitoring_flags_series` | series | v1-v4 and rules A/B of the review app, whole series / 7 days / yesterday |
# MAGIC | `monitoring_change_recent` | series | yesterday vs 14-day baseline, week range, rainfall, wet/dry |
# MAGIC | `monitoring_knmi_daily` | day | KNMI precipitation and Makkink evaporation (forcing CSV) |
# MAGIC | `monitoring_pastas_models` | series x model type x basis | fit, checks, selection, in use |
# MAGIC | `monitoring_pastas_series` | series | model in use (validated and not), map colour |
# MAGIC
# MAGIC The source is read-only; nothing is written to `gold_prd`.

# COMMAND ----------

dbutils.widgets.text("output_catalog", "waterkeringbeheer_prd", "Output catalog")
dbutils.widgets.text("output_schema", "jvanruitenbeek", "Output schema")
dbutils.widgets.text("as_of", "", "Run day, YYYY-MM-DD (blank = today, UTC)")
dbutils.widgets.text("uptime_days", "90", "Days of uptime to (re)compute")
dbutils.widgets.text("station", "249", "KNMI station of the forcing CSV")
dbutils.widgets.text("swing_m", "0.5", "Week range (m) that counts as a big swing")
dbutils.widgets.text("little_rain_mm", "10", "Below this much rain in that week, a swing is suspect")
dbutils.widgets.text("batch_size", "50", "Series per batch for the flag checks")
dbutils.widgets.text("repo_root", "", "Repo root (blank = find it from this notebook)")

# COMMAND ----------

import datetime as dt
import os
import sys
from pathlib import Path


def find_repo_root() -> Path:
    here = Path(os.getcwd()).resolve()
    for candidate in (here, *here.parents):
        if (candidate / "monitoring").is_dir() and (candidate / "data_validation").is_dir():
            return candidate
    raise RuntimeError(f"no ancestor of {here} holds monitoring/ and data_validation/")


ROOT = Path(dbutils.widgets.get("repo_root").strip() or find_repo_root())
for sub in ("monitoring/src", "data_validation", "data_validation/databricks_code/lib", "pastas/src"):
    if str(ROOT / sub) not in sys.path:
        sys.path.insert(0, str(ROOT / sub))

import pandas as pd  # noqa: E402

import source_read as sr  # noqa: E402
from monitoring_lib import flags, sql  # noqa: E402
from tarso_pipeline.forcing import DATE_COLUMN, EVAP_COLUMN, PREC_COLUMN  # noqa: E402
from tarso_pipeline.paths import FORCING_VOLUME_DIR, VALIDATED_TABLE, forcing_volume_path  # noqa: E402

CATALOG = dbutils.widgets.get("output_catalog").strip()
SCHEMA = dbutils.widgets.get("output_schema").strip()
AS_OF = (dt.date.fromisoformat(dbutils.widgets.get("as_of").strip()) if dbutils.widgets.get("as_of").strip()
         else dt.datetime.now(dt.timezone.utc).date())
UPTIME_DAYS = int(dbutils.widgets.get("uptime_days"))
STATION = dbutils.widgets.get("station").strip()
SWING_M = float(dbutils.widgets.get("swing_m"))
LITTLE_RAIN_MM = float(dbutils.widgets.get("little_rain_mm"))
BATCH = int(dbutils.widgets.get("batch_size"))
T = {kind: sql.table(kind, CATALOG, SCHEMA) for kind in sql.TABLES}
print(f"as_of {AS_OF} | root {ROOT} | writing {CATALOG}.{SCHEMA}.monitoring_*")


def exists(name: str) -> bool:
    return spark.catalog.tableExists(name)


def write(frame, name: str, **options) -> None:
    writer = frame.write.format("delta").mode("overwrite").option("overwriteSchema", "true")
    for key, value in options.items():
        writer = writer.option(key, value)
    writer.saveAsTable(name)
    print(f"{name}: {spark.table(name).count():,} rows")

# COMMAND ----------

# MAGIC %md ## 1. Hourly heads, catalog, validated hours

# COMMAND ----------

write(spark.sql(sql.hourly_sql()), T["hourly"])
spark.table(T["hourly"]).createOrReplaceTempView(sql.HOURLY_VIEW)
spark.sql(sr.series_catalog_sql(include_location=True)).createOrReplaceTempView(sql.CATALOG_VIEW)
VALIDATED = VALIDATED_TABLE if exists(VALIDATED_TABLE) else None
spark.sql(sql.validated_hours_sql(VALIDATED)).createOrReplaceTempView(sql.VALIDATED_VIEW)
print("validated_meting:", VALIDATED or "not there yet (every series counts as not validated)")

# COMMAND ----------

# MAGIC %md ## 2. Status per series

# COMMAND ----------

PASTAS = {prefix: {kind: exists(f"{CATALOG}.{SCHEMA}.{prefix}_{kind}")
                   for kind in ("fit_metrics", "model_checks", "model_selection", "model_in_use")}
          for prefix in ("valid_pastas", "unvalid_pastas")}
print(PASTAS)


def fit_table(prefix: str) -> str | None:
    return f"{CATALOG}.{SCHEMA}.{prefix}_fit_metrics" if PASTAS[prefix]["fit_metrics"] else None


write(spark.sql(sql.series_status_sql(AS_OF, fit_table("valid_pastas"), fit_table("unvalid_pastas"))),
      T["series_status"])
display(spark.sql(f"""SELECT leverancier, status, count(*) AS reeksen, count_if(gematcht) AS gematcht
                      FROM {T['series_status']} WHERE NOT is_handwaarneming GROUP BY 1, 2 ORDER BY 1, 2"""))

# COMMAND ----------

# MAGIC %md ## 3. Uptime per sensor per day
# MAGIC Only the last `uptime_days` days are recomputed (`replaceWhere`); older days stay, so
# MAGIC the table builds up a history for the supplier contract.

# COMMAND ----------

uptime = spark.sql(sql.uptime_daily_sql(T["series_status"], AS_OF, UPTIME_DAYS))
start = (AS_OF - dt.timedelta(days=UPTIME_DAYS)).isoformat()
if exists(T["uptime_daily"]):
    (uptime.write.format("delta").mode("overwrite")
     .option("replaceWhere", f"datum >= DATE'{start}'").saveAsTable(T["uptime_daily"]))
    print(f"{T['uptime_daily']}: {spark.table(T['uptime_daily']).count():,} rows")
else:
    write(uptime, T["uptime_daily"])

# COMMAND ----------

# MAGIC %md ## 4. Flags (v1-v4, rules A/B), with the review app's own checks
# MAGIC `local_validation.checks.validate_series` per series on the driver, in batches.

# COMMAND ----------

ids = [r.meetreeks_id for r in spark.sql(
    f"SELECT meetreeks_id FROM {T['series_status']} WHERE NOT is_handwaarneming AND uren_totaal > 0").collect()]
rows = []
for start_at in range(0, len(ids), BATCH):
    batch = spark.sql(sql.flag_input_sql(T["series_status"], ids[start_at:start_at + BATCH])).toPandas()
    for _, group in batch.groupby("meetreeks_id", sort=False):
        rows.append(flags.flag_counts_frame(group, AS_OF))
    print(f"flags: {min(start_at + BATCH, len(ids))} / {len(ids)} series")
flag_frame = (pd.concat(rows, ignore_index=True) if rows
              else pd.DataFrame(columns=list(flags.FLAG_SCHEMA)))
write(spark.createDataFrame(flags.typed(flag_frame), schema=flags.spark_schema()), T["flags_series"])

# COMMAND ----------

# MAGIC %md ## 5. KNMI rainfall and the recent change per series

# COMMAND ----------

KNMI_FILE = forcing_volume_path(STATION)
try:
    knmi = pd.read_csv(KNMI_FILE)[[DATE_COLUMN, PREC_COLUMN, EVAP_COLUMN]]
    knmi = knmi.rename(columns={DATE_COLUMN: "datum"})
    knmi["datum"] = pd.to_datetime(knmi["datum"]).dt.date
    write(spark.createDataFrame(knmi.assign(station=STATION)), T["knmi_daily"])
    KNMI = T["knmi_daily"]
except (FileNotFoundError, KeyError, ValueError) as problem:
    print(f"No usable forcing CSV at {KNMI_FILE} (see {FORCING_VOLUME_DIR}): "
          f"{type(problem).__name__}: {problem}. Rainfall stays as it was.")
    KNMI = T["knmi_daily"] if exists(T["knmi_daily"]) else None

write(spark.sql(sql.change_recent_sql(T["series_status"], AS_OF, KNMI, SWING_M, LITTLE_RAIN_MM)),
      T["change_recent"])

# COMMAND ----------

# MAGIC %md ## 6. Pastas: every model, and per series the model in use and the map colour

# COMMAND ----------

parts = [spark.sql(sql.pastas_models_sql(
             prefix, PASTAS[prefix], CATALOG, SCHEMA,
             fit_columns=set(spark.table(f"{CATALOG}.{SCHEMA}.{prefix}_fit_metrics").columns)))
         for prefix in PASTAS if PASTAS[prefix]["fit_metrics"]]
if parts:
    models = parts[0]
    for part in parts[1:]:
        models = models.unionByName(part)
    write(models, T["pastas_models"])
    write(spark.sql(sql.pastas_series_sql(T["pastas_models"], T["series_status"])), T["pastas_series"])
    display(spark.sql(f"SELECT status_kleur, count(*) AS reeksen FROM {T['pastas_series']} GROUP BY 1 ORDER BY 1"))
else:
    print("No Pastas fit_metrics table yet; the Pastas tables are skipped.")
