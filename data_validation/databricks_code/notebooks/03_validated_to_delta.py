# Databricks notebook source
# MAGIC %md
# MAGIC # 03 · Validated data -> Delta table
# MAGIC Closes the loop. After the locally-validated copies in `input_output/output_repository/`
# MAGIC have been pushed from the IDE and pulled into this Git folder, reads them, keeps the
# MAGIC approved hours, and writes them as a Delta table **shaped like the source `meting`
# MAGIC table**, so anything that reads `gold_prd.hl_grondwaterpeilmeting.meting` can read the
# MAGIC validated data by changing only the table name (the Pastas pipeline does this with
# MAGIC `data_basis=validated`). The source is still never touched.
# MAGIC
# MAGIC Output: `waterkeringbeheer_prd.jvanruitenbeek.validated_meting`, one row per approved hour:
# MAGIC
# MAGIC | column | as in `meting` | meaning |
# MAGIC |---|---|---|
# MAGIC | `meetreeks_id` | yes | the series key |
# MAGIC | `tijdstempel` | yes | the hour, Europe/Amsterdam wall clock |
# MAGIC | `tijdstempel_utc` | yes | the hour in UTC (the key the review store uses) |
# MAGIC | `waarde` | yes | the **validated** head: `raw_data + correctie`, m NAP |
# MAGIC | `validatie_status` | yes | `2` (*Valide* in `validatiestatus`) for every row |
# MAGIC | `waarde_ruw` | no | the hourly source value before the correction |
# MAGIC | `correctie` | no | the manual offset in m (0 when none) |
# MAGIC | `note`, `vendor`, `filename`, `run_id`, `validated_at` | no | provenance |
# MAGIC
# MAGIC Values are hourly means (the review store's grain), not the source's raw ~55 rows a day.
# MAGIC
# MAGIC **Overwrite by default**: the review store is merged into, never replaced, so each run's
# MAGIC table is the complete current picture; earlier versions stay readable with Delta time
# MAGIC travel (`VERSION AS OF`). `write_mode=append` keeps the old run-tagged behaviour, but then
# MAGIC readers must filter on `run_id`, or they see every hour once per run.
# MAGIC
# MAGIC Review files are matched to a series by name:
# MAGIC * **flat** `output_repository/<filename>.csv` — `filename` from
# MAGIC   `output_databricks/metadata/object_data.csv` (the layout the export uses), or
# MAGIC * **nested** `output_repository/<vendor>/<origin>/<series>.csv` — the on-prem layout,
# MAGIC   kept so migrated review work still loads.

# COMMAND ----------
import os
import sys
from datetime import datetime, timezone

dbutils.widgets.text("output_catalog", "waterkeringbeheer_prd")
dbutils.widgets.text("output_schema", "jvanruitenbeek")
dbutils.widgets.text("keep_only_approved", "true")  # gecontroleerd==1 & afgekeurd!=1
dbutils.widgets.dropdown("write_mode", "overwrite", ["overwrite", "append"])
dbutils.widgets.text("table_name", "validated_meting")
dbutils.widgets.text("run_id", "")
dbutils.widgets.text("repo_root", "")

OUT = f'{dbutils.widgets.get("output_catalog")}.{dbutils.widgets.get("output_schema")}'
KEEP_APPROVED = dbutils.widgets.get("keep_only_approved").lower() == "true"
WRITE_MODE = dbutils.widgets.get("write_mode")
TABLE = f'{OUT}.{dbutils.widgets.get("table_name").strip()}'
VALIDE = 2  # validatiestatus: 0 Niet gevalideerd, 1 Invalide, 2 Valide
RUN_ID = dbutils.widgets.get("run_id").strip() or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

ROOT = dbutils.widgets.get("repo_root").strip() or os.getcwd()
while not os.path.isdir(os.path.join(ROOT, "databricks_code", "lib")):
    parent = os.path.dirname(ROOT)
    if parent == ROOT:
        raise FileNotFoundError("Could not find databricks_code/lib above the notebook; set the repo_root widget.")
    ROOT = parent
sys.path.insert(0, os.path.join(ROOT, "databricks_code", "lib"))
import source_read as sr  # noqa: E402

VALIDATION_DATA = os.path.join(ROOT, "input_output", "output_repository")
OBJECT_DATA = os.path.join(ROOT, "input_output", "output_databricks", "metadata", "object_data.csv")

# The store's timestamps are naive UTC hours; a UTC session keeps Spark from shifting them.
spark.conf.set("spark.sql.session.timeZone", "UTC")

# COMMAND ----------
# Name -> meetreeks_id maps: flat `filename` from object_data.csv, nested from the dimension.
import pandas as pd  # noqa: E402

file_to_id = {}
if os.path.exists(OBJECT_DATA):
    od = pd.read_csv(OBJECT_DATA, usecols=["filename", "meetreeks_id", "vendor"])
    file_to_id = {r.filename: (r.meetreeks_id, r.vendor) for r in od.itertuples(index=False)}

dim = spark.table(f"{OUT}.source_copy_meetreeks").toPandas()
key_to_id = {}
for _, r in dim.iterrows():
    sn = sr.name_series(r["meetreeks_id"], r.get("code"), r.get("bronsysteem"), r.get("sensor_sleutel"))
    key_to_id[(sn.vendor, sn.origin, sn.series)] = (r["meetreeks_id"], sn.vendor)
print(f"{len(file_to_id)} flat names, {len(key_to_id)} nested names")

# COMMAND ----------
# Read review CSVs (utf-8-sig; all three verdict columns required).
REQUIRED = {"raw_data", "gecontroleerd", "afgekeurd"}
frames, skipped = [], []
for dirpath, _dirs, files in os.walk(VALIDATION_DATA):
    for fn in files:
        if not fn.endswith(".csv") or fn.lower() == "object_data.csv":
            continue
        path = os.path.join(dirpath, fn)
        rel = os.path.relpath(path, VALIDATION_DATA).replace("\\", "/").split("/")
        stem = os.path.splitext(rel[-1])[0]
        if len(rel) == 1:
            hit = file_to_id.get(stem)
        elif len(rel) >= 3:
            hit = key_to_id.get((rel[0], rel[1], stem))
        else:
            hit = None
        if hit is None:
            skipped.append((path, "no matching series")); continue
        mid, vendor = hit

        df = pd.read_csv(path, encoding="utf-8-sig")
        df.columns = [c.strip().lower() for c in df.columns]
        if not REQUIRED.issubset(df.columns):
            skipped.append((path, "missing verdict columns (treated as raw/unreviewed)")); continue

        df["timestamp"] = pd.to_datetime(df["timestamp"])
        df["gecontroleerd"] = pd.to_numeric(df["gecontroleerd"], errors="coerce").fillna(0).astype(int)
        df["afgekeurd"] = pd.to_numeric(df["afgekeurd"], errors="coerce").fillna(0).astype(int)
        # Stores saved before the column existed have no correction.
        df["correctie"] = (pd.to_numeric(df["correctie"], errors="coerce").fillna(0.0)
                           if "correctie" in df.columns else 0.0)
        if KEEP_APPROVED:
            df = df[(df["gecontroleerd"] == 1) & (df["afgekeurd"] != 1)]
        if df.empty:
            continue
        df = df.dropna(subset=["raw_data"])
        out = pd.DataFrame({
            "meetreeks_id": mid,
            "tijdstempel_utc": df["timestamp"],
            "waarde": df["raw_data"] + df["correctie"],
            "validatie_status": VALIDE,
            "waarde_ruw": df["raw_data"],
            "correctie": df["correctie"],
            "note": df["note"].fillna("").astype(str) if "note" in df.columns else "",
            "vendor": vendor,
            "filename": stem,
        })
        frames.append(out)

print(f"matched {len(frames)} series; skipped {len(skipped)}")
for p, why in skipped[:20]:
    print("  skip:", why, "->", p)

# COMMAND ----------
# Write validated_meting: the source's column names first, then the provenance.
from pyspark.sql import functions as F  # noqa: E402

if not frames:
    dbutils.notebook.exit("0")
validated = pd.concat(frames, ignore_index=True)
validated["run_id"] = RUN_ID
print(f"{len(validated):,} approved hours in {validated['meetreeks_id'].nunique()} series, "
      f"{int((validated['correctie'] != 0).sum()):,} of them corrected")

sdf = (spark.createDataFrame(validated)
       .withColumn("tijdstempel_utc", F.col("tijdstempel_utc").cast("timestamp"))
       .withColumn("tijdstempel", F.from_utc_timestamp("tijdstempel_utc", "Europe/Amsterdam"))
       .withColumn("validatie_status", F.col("validatie_status").cast("int"))
       .withColumn("validated_at", F.current_timestamp())
       .select("meetreeks_id", "tijdstempel", "tijdstempel_utc", "waarde", "validatie_status",
               "waarde_ruw", "correctie", "note", "vendor", "filename", "run_id", "validated_at"))
writer = sdf.write.format("delta").mode(WRITE_MODE).partitionBy("vendor")
writer = writer.option("overwriteSchema", "true") if WRITE_MODE == "overwrite" else writer.option("mergeSchema", "true")
writer.saveAsTable(TABLE)
print(f"{WRITE_MODE}: {TABLE}")

display(spark.sql(f"""
    SELECT vendor, count(DISTINCT meetreeks_id) AS series, count(*) AS approved_hours,
           count_if(correctie <> 0) AS corrected_hours, min(tijdstempel) AS first, max(tijdstempel) AS last
    FROM {TABLE} WHERE run_id = '{RUN_ID}' GROUP BY vendor ORDER BY vendor
"""))
dbutils.notebook.exit(str(len(validated)))
