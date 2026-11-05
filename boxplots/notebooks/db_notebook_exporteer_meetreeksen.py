# Databricks notebook source
# MAGIC %md
# MAGIC # db_notebook - Alle meetreeksen met hun meetlocatie exporteren naar csv
# MAGIC
# MAGIC Stap 1 van een nieuw dwarsprofiel: wat staat er op het platform? Schrijft
# MAGIC `input_data/databricks_export/meetreeksen.csv`, één rij per meetreeks, met
# MAGIC de meetlocatie, de ligging (RD), de filters en het aantal metingen. Dit is
# MAGIC de DD-kant van `notebooks/bouw_koppeltabel_ld_dd.py`.
# MAGIC
# MAGIC Het notebook leest **niets** uit onze eigen invoer (`config.csv`): het kan
# MAGIC dus vóór het invullen van nieuwe peilbuizen draaien. Zo zie je eerst of een
# MAGIC put op het platform staat. De selectie op afstand en naam gebeurt lokaal,
# MAGIC in de koppeltabel.
# MAGIC
# MAGIC De koppeling meetreeks -> meetlocatie is dezelfde als in
# MAGIC `db_notebook_exporteer_stijghoogtes.py` en `modules/databricks_live.py`: via
# MAGIC `meetlocatie_object_id`, dan `meetlocatie_code`, dan de code van de reeks.
# MAGIC Daarna committen en pushen vanuit de Git folder. De tabellen en kolommen
# MAGIC staan in `DATA_CATALOG.md`.

# COMMAND ----------

dbutils.widgets.text("tel_metingen", "ja", "Aantal metingen per reeks tellen (ja/nee)")

# COMMAND ----------

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
from pyspark.sql import functions as F  # noqa: E402

from modules import paths  # noqa: E402

TEL_METINGEN: bool = dbutils.widgets.get("tel_metingen").strip().lower() in ("ja", "j", "yes", "true", "1")
UITVOER: Path = paths.export_map() / "meetreeksen.csv"

print(f"repo {REPO_ROOT} | uitvoer {UITVOER} | metingen tellen: {TEL_METINGEN}")

# COMMAND ----------

# MAGIC %md ## Alle meetreeksen met hun meetlocatie

# COMMAND ----------

alle = spark.sql(f"""
    SELECT r.id AS meetreeks_id, trim(r.code) AS code, r.bronsysteem,
           r.sensor_meetlocatie_sleutel AS sensor_sleutel,
           CAST(r.begin_filter AS DOUBLE) AS begin_filter,
           CAST(r.eind_filter AS DOUBLE) AS eind_filter,
           CAST(r.bovenkant_peilbuis AS DOUBLE) AS bovenkant_peilbuis,
           r.object_id, r.eenheid, r.eigenschap_id, r.meetlocatie_object_id, r.meetlocatie_code,
           r.validatie_status,
           l.id AS cs_meetlocatie_id, l.code AS cs_code, l.naam AS cs_naam,
           l.compartiment_code, l.meetreeks_actief,
           CAST(l.rd_xcoordinaat AS DOUBLE) AS rd_x, CAST(l.rd_ycoordinaat AS DOUBLE) AS rd_y
    FROM {paths.TBL_MEETREEKS} r
    LEFT JOIN {paths.TBL_MEETLOCATIE} l_id   ON l_id.id     = r.meetlocatie_object_id
    LEFT JOIN {paths.TBL_MEETLOCATIE} l_code ON l_code.code = r.meetlocatie_code
    LEFT JOIN {paths.TBL_MEETLOCATIE} l_naam ON l_naam.code = trim(r.code)
    LEFT JOIN {paths.TBL_MEETLOCATIE} l      ON l.id = coalesce(l_id.id, l_code.id, l_naam.id)
""").dropDuplicates(["meetreeks_id"])
print(f"{alle.count():,} meetreeksen op het platform")

# COMMAND ----------

# MAGIC %md ## Aantal metingen per reeks, en wegschrijven

# COMMAND ----------

# Het tellen leest de hele meting-tabel; met tel_metingen = nee blijft n_rows
# leeg en kiest de koppeltabel minder goed tussen parallelle sensoren.
if TEL_METINGEN:
    tellingen = (
        spark.table(paths.TBL_METING)
        .filter(F.col("waarde").isNotNull())
        .groupBy("meetreeks_id")
        .agg(F.countDistinct("tijdstempel_utc").alias("n_rows"))
    )
    alle = alle.join(tellingen, "meetreeks_id", "left").fillna({"n_rows": 0})
else:
    alle = alle.withColumn("n_rows", F.lit(None).cast("long"))

catalogus: pd.DataFrame = (
    alle.orderBy(F.col("cs_code").asc_nulls_last(), "code", "meetreeks_id").toPandas()
)
display(catalogus.head(50))

UITVOER.parent.mkdir(parents=True, exist_ok=True)
catalogus.to_csv(UITVOER, index=False)
print(f"{UITVOER.relative_to(REPO_ROOT)}: {len(catalogus):,} rijen, {UITVOER.stat().st_size / 1e6:.1f} MB")
print("\nCommit en push nu input_data/databricks_export/meetreeksen.csv vanuit de Git folder.")
