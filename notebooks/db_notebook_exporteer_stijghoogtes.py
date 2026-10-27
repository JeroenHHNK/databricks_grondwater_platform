# Databricks notebook source
# MAGIC %md
# MAGIC # db_notebook - Stijghoogtes en reeksinformatie exporteren naar csv
# MAGIC
# MAGIC Haalt voor elke peilbuis met een `meetlocatie_id` in `input_data/config.csv`
# MAGIC de meetreeksen en metingen op, en schrijft ze als csv in de Git folder:
# MAGIC `input_data/databricks_export/`. Daarna committen en pushen vanuit de Git
# MAGIC folder, zodat de bestanden lokaal te gebruiken zijn.
# MAGIC
# MAGIC | bestand | inhoud |
# MAGIC |---|---|
# MAGIC | `reeksen.csv` | per peilbuis per meetreeks: `reeks_nr`, code, eenheid, filters, `validatie_status` van de reeks, aantal metingen (en per status), eerste/laatste, gem/min/max, meetinterval |
# MAGIC | `stijghoogtes_dag.csv` | per `reeks_nr` per dag (UTC): aantal, gemiddelde, minimum, maximum; aantal per `validatie_status` en het gemiddelde van alleen status 2 |
# MAGIC | `afwijkingen.csv` | ruwe metingen die meer dan `drempel_m` van de dagmediaan van hun reeks afwijken, met hun `validatie_status` |
# MAGIC | `schema.csv` | kolommen van `meting`, `meetreeks`, `validatiestatus`, `validatieresultaat` |
# MAGIC | `validatie_tellingen.csv` | per status-/validatiekolom de waarden en hun aantal |
# MAGIC | `validatiestatus.csv` | de hele tabel `validatiestatus`: wat elke status betekent |
# MAGIC | `validatieresultaat.csv` | `validatieresultaat` samengevat per tabel, kolom, type, ernst en bericht (zonder `pk`) |
# MAGIC
# MAGIC Metingen worden ontdubbeld op `meetreeks_id + tijdstempel_utc` (`meting.id` is
# MAGIC niet uniek). De koppeling meetreeks -> meetlocatie is dezelfde als in
# MAGIC notebook 20: via `meetlocatie_object_id`, dan `meetlocatie_code`, dan de code.
# MAGIC
# MAGIC Er gaan geen persoonsnamen mee: kolommen met `door`, `naam`, `gebruiker`,
# MAGIC `user` of `email` in de naam worden niet geteld.

# COMMAND ----------

dbutils.widgets.text("projecten", "", "Projecten (komma-gescheiden, leeg = alle)")
dbutils.widgets.text("drempel_m", "0.5", "Afwijking t.o.v. dagmediaan (m)")
dbutils.widgets.text("max_afwijkingen", "50000", "Maximaal aantal afwijkingen")

# COMMAND ----------

import re
import sys
from pathlib import Path


def zoek_repo_root() -> Path:
    for kandidaat in [Path.cwd(), *Path.cwd().parents]:
        if (kandidaat / "pyproject.toml").is_file():
            return kandidaat
    raise FileNotFoundError(f"Geen pyproject.toml gevonden vanaf {Path.cwd()}")


REPO_ROOT = zoek_repo_root()
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import pandas as pd  # noqa: E402
from pyspark.sql import DataFrame, Window, functions as F  # noqa: E402

from Modules import paths  # noqa: E402

GEVRAAGD: str = dbutils.widgets.get("projecten").strip()
DREMPEL_M: float = float(dbutils.widgets.get("drempel_m"))
MAX_AFWIJKINGEN: int = int(dbutils.widgets.get("max_afwijkingen"))
UITMAP: Path = REPO_ROOT / "input_data" / "databricks_export"

print(f"repo {REPO_ROOT} | uitmap {UITMAP} | drempel {DREMPEL_M} m")

# COMMAND ----------

# MAGIC %md ## De peilbuizen uit config.csv

# COMMAND ----------

config: pd.DataFrame = pd.read_csv(REPO_ROOT / "input_data" / "config.csv", dtype=str)
if GEVRAAGD:
    config = config[config["project"].isin([p.strip() for p in GEVRAAGD.split(",") if p.strip()])]
putten_pd: pd.DataFrame = config.loc[
    config["meetlocatie_id"].notna(),
    ["project", "cross_section_key", "diepte_niveau", "peilbuis", "meetlocatie_id"],
]
print(f"{len(config)} peilbuizen in config.csv, {len(putten_pd)} met meetlocatie_id")

spark.createDataFrame(putten_pd).createOrReplaceTempView("putten")

spark.sql(f"""
    CREATE OR REPLACE TEMP VIEW reeks_locatie AS
    SELECT r.id AS meetreeks_id, trim(r.code) AS meetreeks_code, r.eenheid,
           CAST(r.begin_filter AS DOUBLE) AS begin_filter,
           CAST(r.eind_filter AS DOUBLE) AS eind_filter,
           CAST(r.bovenkant_peilbuis AS DOUBLE) AS bovenkant_peilbuis,
           r.bronsysteem, r.validatie_status AS reeks_validatie_status,
           coalesce(l_id.id, l_code.id, l_naam.id) AS meetlocatie_id,
           CASE WHEN l_id.id IS NOT NULL THEN 'object_id'
                WHEN l_code.id IS NOT NULL THEN 'locatie_code'
                WHEN l_naam.id IS NOT NULL THEN 'code_gelijk' END AS brug
    FROM {paths.TBL_MEETREEKS} r
    LEFT JOIN {paths.TBL_MEETLOCATIE} l_id   ON l_id.id     = r.meetlocatie_object_id
    LEFT JOIN {paths.TBL_MEETLOCATIE} l_code ON l_code.code = r.meetlocatie_code
    LEFT JOIN {paths.TBL_MEETLOCATIE} l_naam ON l_naam.code = trim(r.code)
""")

put_reeksen: DataFrame = spark.sql("""
    SELECT p.project, p.cross_section_key, p.diepte_niveau, p.peilbuis, p.meetlocatie_id,
           rl.meetreeks_id, rl.meetreeks_code, rl.eenheid, rl.begin_filter, rl.eind_filter,
           rl.bovenkant_peilbuis, rl.bronsysteem, rl.reeks_validatie_status, rl.brug
    FROM putten p
    LEFT JOIN reeks_locatie rl ON rl.meetlocatie_id = p.meetlocatie_id
""").cache()

zonder_reeks = put_reeksen.filter(F.col("meetreeks_id").isNull()).count()
print(f"{put_reeksen.select('meetreeks_id').distinct().count()} meetreeksen; "
      f"{zonder_reeks} peilbuizen zonder enige meetreeks")

# COMMAND ----------

# MAGIC %md ## Metingen, ontdubbeld

# COMMAND ----------

ids: DataFrame = put_reeksen.select("meetreeks_id").where(F.col("meetreeks_id").isNotNull()).distinct()
metingen: DataFrame = (
    spark.table(paths.TBL_METING)
    .select("meetreeks_id", "tijdstempel_utc", "waarde", "validatie_status")
    .join(ids, "meetreeks_id", "inner")
    .filter(F.col("waarde").isNotNull())
    .dropDuplicates(["meetreeks_id", "tijdstempel_utc"])
    .withColumn("datum", F.to_date("tijdstempel_utc"))
    .cache()
)
print(f"{metingen.count():,} metingen")

# COMMAND ----------

# MAGIC %md ## 1. reeksen.csv

# COMMAND ----------

interval: DataFrame = (
    metingen.withColumn(
        "dt_min",
        (F.col("tijdstempel_utc").cast("long")
         - F.lag("tijdstempel_utc").over(Window.partitionBy("meetreeks_id").orderBy("tijdstempel_utc")).cast("long"))
        / 60.0,
    )
    .groupBy("meetreeks_id")
    .agg(
        F.count("*").alias("metingen"),
        F.min("tijdstempel_utc").alias("van"),
        F.max("tijdstempel_utc").alias("tot"),
        F.round(F.avg("waarde"), 4).alias("gem"),
        F.round(F.min("waarde"), 4).alias("min"),
        F.round(F.max("waarde"), 4).alias("max"),
        F.countDistinct("datum").alias("dagen"),
        F.percentile_approx("dt_min", 0.5).alias("interval_mediaan_min"),
        F.sum(F.when(F.col("validatie_status") == 1, 1).otherwise(0)).alias("n_status_1"),
        F.sum(F.when(F.col("validatie_status") == 2, 1).otherwise(0)).alias("n_status_2"),
        F.sum(F.when(~F.col("validatie_status").isin(1, 2) | F.col("validatie_status").isNull(), 1).otherwise(0)).alias("n_status_overig"),
    )
)
reeksen_pd: pd.DataFrame = (
    put_reeksen.join(interval, "meetreeks_id", "left")
    .orderBy("project", "cross_section_key", "diepte_niveau", "peilbuis", "van")
    .toPandas()
)
display(reeksen_pd)

# COMMAND ----------

# MAGIC %md ## 2. stijghoogtes_dag.csv

# COMMAND ----------

dag_pd: pd.DataFrame = (
    metingen.groupBy("meetreeks_id", "datum")
    .agg(
        F.count("*").alias("n"),
        F.round(F.avg("waarde"), 4).alias("gem"),
        F.round(F.min("waarde"), 4).alias("min"),
        F.round(F.max("waarde"), 4).alias("max"),
        F.sum(F.when(F.col("validatie_status") == 1, 1).otherwise(0)).alias("n_status_1"),
        F.sum(F.when(F.col("validatie_status") == 2, 1).otherwise(0)).alias("n_status_2"),
        F.sum(F.when(~F.col("validatie_status").isin(1, 2) | F.col("validatie_status").isNull(), 1).otherwise(0)).alias("n_status_overig"),
        F.round(F.avg(F.when(F.col("validatie_status") == 2, F.col("waarde"))), 4).alias("gem_status_2"),
    )
    .orderBy("meetreeks_id", "datum")
    .toPandas()
)
print(f"{len(dag_pd):,} dagwaarden")

# COMMAND ----------

# MAGIC %md ## 3. afwijkingen.csv

# COMMAND ----------

dagmediaan: DataFrame = metingen.groupBy("meetreeks_id", "datum").agg(
    F.percentile_approx("waarde", 0.5).alias("dagmediaan")
)
afwijkingen_pd: pd.DataFrame = (
    metingen.join(dagmediaan, ["meetreeks_id", "datum"])
    .withColumn("afwijking", F.round(F.col("waarde") - F.col("dagmediaan"), 4))
    .filter(F.abs("afwijking") > DREMPEL_M)
    .select("meetreeks_id", "tijdstempel_utc", "waarde", "validatie_status", "dagmediaan", "afwijking")
    .orderBy("meetreeks_id", "tijdstempel_utc")
    .limit(MAX_AFWIJKINGEN)
    .toPandas()
)
print(f"{len(afwijkingen_pd):,} metingen wijken meer dan {DREMPEL_M} m af van hun dagmediaan")

# COMMAND ----------

# MAGIC %md ## 4. schema.csv en validatie_tellingen.csv

# COMMAND ----------

TABELLEN: list[str] = [
    paths.TBL_METING, paths.TBL_MEETREEKS, paths.TBL_VALIDATIESTATUS, paths.TBL_VALIDATIERESULTAAT,
]
STATUSKOLOM = re.compile(r"status|valid|kwaliteit|flag|goedgekeurd|afgekeurd|vlag", re.I)
PERSOON = re.compile(r"door|naam|gebruiker|user|email", re.I)

schema_rijen: list[dict] = []
tellingen: list[pd.DataFrame] = []
for tabel in TABELLEN:
    try:
        kolommen = [(k.name, k.dataType.simpleString()) for k in spark.table(tabel).schema.fields]
    except Exception as fout:  # noqa: BLE001 - een ontbrekende tabel mag de export niet stoppen
        schema_rijen.append({"tabel": tabel, "kolom": None, "type": None, "fout": str(fout)[:300]})
        continue
    schema_rijen += [{"tabel": tabel, "kolom": k, "type": t, "fout": None} for k, t in kolommen]

    df: DataFrame = spark.table(tabel)
    if "meetreeks_id" in dict(kolommen):
        df = df.join(ids, "meetreeks_id", "inner")
    for kolom, type_ in kolommen:
        if not STATUSKOLOM.search(kolom) or PERSOON.search(kolom):
            continue
        if not re.match(r"string|int|bigint|smallint|tinyint|boolean", type_):
            continue
        telling = df.groupBy(kolom).count().orderBy(F.desc("count")).limit(50).toPandas()
        telling.columns = ["waarde", "aantal"]
        telling.insert(0, "kolom", kolom)
        telling.insert(0, "tabel", tabel)
        telling["waarde"] = telling["waarde"].astype(str)
        tellingen.append(telling)

schema_pd = pd.DataFrame(schema_rijen)
tellingen_pd = (
    pd.concat(tellingen, ignore_index=True) if tellingen
    else pd.DataFrame(columns=["tabel", "kolom", "waarde", "aantal"])
)
display(schema_pd)
display(tellingen_pd)

# validatiestatus is een kleine opzoektabel (id, omschrijving).
validatiestatus_pd: pd.DataFrame = spark.table(paths.TBL_VALIDATIESTATUS).orderBy("id").toPandas()
# validatieresultaat: samengevat, zonder pk (die wijst naar individuele rijen).
validatieresultaat_pd: pd.DataFrame = (
    spark.table(paths.TBL_VALIDATIERESULTAAT)
    .groupBy("schema", "tabel", "kolom", "verwachting_type", "validatie_type", "ernst", "gebruikersbericht")
    .agg(
        F.count("*").alias("aantal"),
        F.min("validatie_tijd").alias("eerste"),
        F.max("validatie_tijd").alias("laatste"),
    )
    .orderBy(F.desc("aantal"))
    .toPandas()
)
display(validatiestatus_pd)
display(validatieresultaat_pd)

# COMMAND ----------

# MAGIC %md ## Wegschrijven naar de Git folder

# COMMAND ----------

# Een kort reeksnummer in plaats van de 64 tekens lange meetreeks_id in de grote
# bestanden; reeksen.csv koppelt de twee.
nummers: dict[str, int] = {
    mid: nr for nr, mid in enumerate(sorted(reeksen_pd["meetreeks_id"].dropna().unique()), start=1)
}
reeksen_pd.insert(0, "reeks_nr", reeksen_pd["meetreeks_id"].map(nummers).astype("Int64"))
for df in (dag_pd, afwijkingen_pd):
    df.insert(0, "reeks_nr", df.pop("meetreeks_id").map(nummers))

UITMAP.mkdir(parents=True, exist_ok=True)
for naam, df in (
    ("reeksen.csv", reeksen_pd),
    ("stijghoogtes_dag.csv", dag_pd),
    ("afwijkingen.csv", afwijkingen_pd),
    ("schema.csv", schema_pd),
    ("validatie_tellingen.csv", tellingen_pd),
    ("validatiestatus.csv", validatiestatus_pd),
    ("validatieresultaat.csv", validatieresultaat_pd),
):
    pad = UITMAP / naam
    df.to_csv(pad, index=False)
    print(f"  {pad.relative_to(REPO_ROOT)}: {len(df):,} rijen, {pad.stat().st_size / 1e6:.1f} MB")

print("\nCommit en push nu input_data/databricks_export/ vanuit de Git folder.")
