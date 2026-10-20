# Databricks notebook source
# MAGIC %md
# MAGIC # 00 - Omgevingscheck
# MAGIC
# MAGIC Controleert, voordat er iets zwaars draait, of deze pipeline mag wat hij
# MAGIC denkt te mogen. De rechten zijn asymmetrisch: lezen uit `gold_prd`,
# MAGIC schrijven alleen in `waterkeringbeheer_prd`. Een ontbrekend recht kost hier
# MAGIC seconden in plaats van een halve run.
# MAGIC
# MAGIC Schrijft niets blijvends weg. Draai dit eerst, en bij elke nieuwe cluster.
# MAGIC
# MAGIC | Check | Waarom |
# MAGIC |---|---|
# MAGIC | `current_user` / `current_catalog` | `currentCatalog()` geeft de *workspace*-naam terug, geen bruikbare catalog |
# MAGIC | lezen uit de twee bronschema's | het recht op `hl_cs_objecten` is niet eerder bevestigd |
# MAGIC | schrijven in het eigen schema | tabellen zijn de harde eis |
# MAGIC | `WRITE VOLUME` | apart recht, nog onbevestigd; alleen de bestandsexport hangt eraan |
# MAGIC | repo + werkboeken leesbaar | de Git folder moet de invoer geleverd hebben |

# COMMAND ----------

import sys
from pathlib import Path


def zoek_repo_root() -> Path:
    """
    Zoekt de repo-root op aan de hand van pyproject.toml.

    Op Databricks is `__file__` niet gedefinieerd -- workspace-.py-bestanden
    worden met exec uitgevoerd -- dus wordt vanaf de werkmap omhoog gezocht.
    """
    for kandidaat in [Path.cwd(), *Path.cwd().parents]:
        if (kandidaat / "pyproject.toml").is_file():
            return kandidaat
    raise FileNotFoundError(f"Geen pyproject.toml gevonden vanaf {Path.cwd()}")


REPO_ROOT = zoek_repo_root()
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from Modules import paths  # noqa: E402

print(f"repo root : {REPO_ROOT}")
print(f"projecten : {paths.projecten()}")

# COMMAND ----------

# MAGIC %md ## 1. Wie zijn we, en welke catalog is 'de huidige'?

# COMMAND ----------

display(spark.sql("SELECT current_user() AS gebruiker, current_catalog() AS huidige_catalog"))

# MAGIC %md
# MAGIC > `current_catalog()` geeft `dbw_hhnkda_prd_05`: de workspace, niet een catalog
# MAGIC > waarop je kunt queryen. Daarom gebruikt alle code in deze repo expliciete
# MAGIC > drie-delige namen uit `Modules/paths.py`.

# COMMAND ----------

# MAGIC %md ## 2. Lezen uit de bron

# COMMAND ----------

bron_tabellen: dict[str, str] = {
    "meting": paths.TBL_METING,
    "meetreeks": paths.TBL_MEETREEKS,
    "meetlocatie": paths.TBL_MEETLOCATIE,
}

for naam, tabel in bron_tabellen.items():
    try:
        aantal = spark.sql(f"SELECT count(*) AS n FROM {tabel}").collect()[0]["n"]
        print(f"  OK  {naam:<12} {aantal:>12,} rijen   ({tabel})")
    except Exception as fout:  # noqa: BLE001 - we willen hier juist alles zien
        print(f"  AF  {naam:<12} {type(fout).__name__}: {fout}")

# COMMAND ----------

# MAGIC %md
# MAGIC ### De brug meetreeks -> meetlocatie
# MAGIC
# MAGIC Hier hangt van af of een peilbuis coordinaten kan krijgen. In het enige
# MAGIC eerder bekeken `meetreeks`-record waren `meetlocatie_code` en
# MAGIC `meetlocatie_object_id` beide NULL. Als `pct_linkbaar` laag is, moet
# MAGIC notebook 20 terugvallen op `meetlocatie.code = trim(meetreeks.code)`.

# COMMAND ----------

display(
    spark.sql(f"""
        SELECT count(*)                                                  AS reeksen_totaal,
               count(meetlocatie_code)                                   AS heeft_locatie_code,
               count(meetlocatie_object_id)                              AS heeft_locatie_id,
               round(100.0 * count(meetlocatie_object_id) / count(*), 1) AS pct_linkbaar
        FROM {paths.TBL_MEETREEKS}
    """)
)

# COMMAND ----------

# MAGIC %md
# MAGIC ### Openstaande datakwaliteitsmeldingen op het bronschema
# MAGIC
# MAGIC `meting.id` is niet uniek en de bron zegt dat zelf. Deze pipeline gebruikt
# MAGIC `meting.id` niet, maar het is goed om te weten wat er nog openstaat.
# MAGIC Let op: `ernst` is nullable, dus niet filteren op `ernst = 'critical'`.

# COMMAND ----------

display(
    spark.sql(f"""
        SELECT tabel, kolom, verwachting_type, ernst, gebruikersbericht, validatie_tijd
        FROM {paths.TBL_VALIDATIERESULTAAT}
        ORDER BY validatie_tijd DESC
        LIMIT 50
    """)
)

# COMMAND ----------

# MAGIC %md ## 3. Schrijven in het eigen schema

# COMMAND ----------

proef_tabel: str = f"{paths.UIT_CATALOG}.{paths.UIT_SCHEMA}.boxplots__schrijftest"
try:
    spark.sql(f"CREATE TABLE IF NOT EXISTS {proef_tabel} (n INT)")
    spark.sql(f"INSERT INTO {proef_tabel} VALUES (1)")
    gelezen = spark.sql(f"SELECT count(*) AS n FROM {proef_tabel}").collect()[0]["n"]
    spark.sql(f"DROP TABLE {proef_tabel}")
    print(f"  OK  tabellen schrijfbaar in {paths.UIT_CATALOG}.{paths.UIT_SCHEMA} ({gelezen} rij gelezen)")
except Exception as fout:  # noqa: BLE001
    print(f"  AF  tabel schrijven mislukt: {type(fout).__name__}: {fout}")
    print("      Vraag rechten aan op het schema, of pas paths.UIT_SCHEMA aan.")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 4. WRITE VOLUME - het onbevestigde recht
# MAGIC
# MAGIC Schrijfrecht op tabellen zegt niets over schrijfrecht op een volume: dat
# MAGIC zijn losse grants. Alleen de parquet/csv-export hangt hieraan; de
# MAGIC matchrapportage werkt ook zonder.

# COMMAND ----------

import os

proef_bestand: str = paths.volume_pad("__schrijftest.txt")
try:
    os.makedirs(os.path.dirname(proef_bestand), exist_ok=True)
    with open(proef_bestand, "w", encoding="utf-8") as bestand:
        bestand.write("ok")
    os.remove(proef_bestand)
    print(f"  OK  volume schrijfbaar: {paths.VOLUME_BASIS}")
except Exception as fout:  # noqa: BLE001
    print(f"  AF  volume NIET schrijfbaar: {type(fout).__name__}: {fout}")
    print("      Vraag WRITE VOLUME aan op waterkeringbeheer_prd.misc_files.misc_files,")
    print("      of zet de widget volume_base op een pad waar je wel mag schrijven.")
    print("      De tabellen werken hier los van.")

# COMMAND ----------

# MAGIC %md ## 5. Zijn de werkboeken meegekomen met de Git folder?

# COMMAND ----------

import pandas as pd

for project in paths.projecten():
    pad: Path = paths.workbook_pad(project)
    if not pad.is_file():
        print(f"  AF  {project:<24} geen werkboek op {pad}")
        continue
    peilbuizen = pd.read_excel(pad, sheet_name="peilbuizen")
    stix = len(list(paths.stix_map(project).glob("*.stix")))
    met_id = peilbuizen["meetreeks_id"].notna().sum() if "meetreeks_id" in peilbuizen else 0
    print(
        f"  OK  {project:<24} {len(peilbuizen):>3} peilbuizen, {stix:>2} stix, "
        f"{met_id:>3} met meetreeks_id"
    )

# COMMAND ----------

# MAGIC %md
# MAGIC ---
# MAGIC **Klaar als:** alle regels hierboven `OK` zijn. Een `AF` op het volume is
# MAGIC acceptabel om door te gaan; een `AF` op lezen uit de bron of schrijven in
# MAGIC het eigen schema is dat niet.
