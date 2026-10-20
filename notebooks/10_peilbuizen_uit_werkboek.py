# Databricks notebook source
# MAGIC %md
# MAGIC # 10 - Peilbuizen uit de werkboeken naar Delta
# MAGIC
# MAGIC Leest per project `input_data/<project>/<project>.xlsx` (geleverd door de
# MAGIC Git folder) en zet de bladen `profielen` en `peilbuizen` in twee tabellen.
# MAGIC Dat is de "onze kant"-helft van het matchprobleem; notebook 20 zet daar de
# MAGIC platformkant tegenover.
# MAGIC
# MAGIC Beide tabellen worden **overschreven**: ze houden de laatste stand van de
# MAGIC werkboeken bij, geen historie. Het werkboek is de bron van waarheid.

# COMMAND ----------

dbutils.widgets.text("projecten", "", "Projecten (komma-gescheiden, leeg = alle)")
dbutils.widgets.dropdown("schrijven", "true", ["true", "false"], "Tabellen schrijven")

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

from Modules import paths, platform_match  # noqa: E402

GEVRAAGD: str = dbutils.widgets.get("projecten").strip()
PROJECTEN: list[str] = (
    [p.strip() for p in GEVRAAGD.split(",") if p.strip()] if GEVRAAGD else paths.projecten()
)
SCHRIJVEN: bool = dbutils.widgets.get("schrijven") == "true"

print(f"projecten: {PROJECTEN}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Inlezen en normaliseren
# MAGIC
# MAGIC De normalisatiekolommen worden hier al berekend zodat notebook 20 alleen
# MAGIC nog hoeft te joinen. `canoniek` haalt scheidingstekens en voorloopnullen
# MAGIC weg: `HB002PB01` en `'HB2-PB1 '` komen beide op `HB2PB1` uit.

# COMMAND ----------

profiel_rijen: list[dict] = []
peilbuis_rijen: list[dict] = []

for project in PROJECTEN:
    pad: Path = paths.workbook_pad(project)
    if not pad.is_file():
        print(f"  overslaan: {project} heeft geen werkboek ({pad})")
        continue

    profielen: pd.DataFrame = pd.read_excel(pad, sheet_name="profielen")
    peilbuizen: pd.DataFrame = pd.read_excel(pad, sheet_name="peilbuizen")
    projectinfo: pd.DataFrame = pd.read_excel(pad, sheet_name="project")
    projectnummer: str = str(
        dict(zip(projectinfo["key"], projectinfo["value"])).get("projectnummer", "")
    )

    for _, rij in profielen.iterrows():
        profiel_rijen.append(
            {
                "project": project,
                "profiel_id": str(rij["profiel_id"]),
                "naam": str(rij["naam"]),
                "hoofdprofiel_id": "" if pd.isna(rij.get("hoofdprofiel_id")) else str(rij["hoofdprofiel_id"]),
                "diepte_niveau": str(rij.get("diepte_niveau", "")),
                "x_rd": None if pd.isna(rij.get("x_rd")) else float(rij["x_rd"]),
                "y_rd": None if pd.isna(rij.get("y_rd")) else float(rij["y_rd"]),
                "stix_bestand": "" if pd.isna(rij.get("stix_bestand")) else str(rij["stix_bestand"]),
            }
        )

    for _, rij in peilbuizen.iterrows():
        naam: str = str(rij["peilbuis_naam"])
        code: str = "" if pd.isna(rij.get("code_peilbuis")) else str(rij["code_peilbuis"])
        putdeel, filterdeel = platform_match.put_en_filter(naam)
        peilbuis_rijen.append(
            {
                "project": project,
                "projectnummer": projectnummer,
                "profiel_id": str(rij["profiel_id"]),
                "positie": int(rij["positie"]),
                "zone": "" if pd.isna(rij.get("zone")) else str(rij["zone"]),
                "peilbuis_naam": naam,
                "code_peilbuis": code,
                # normalisaties voor notebook 20
                "naam_canoniek": platform_match.canoniek(naam),
                "put_canoniek": platform_match.canoniek(putdeel),
                "filter_nummer": filterdeel or platform_match.pb_nummer(code),
                "filter_uit_code_m": platform_match.filter_diepte_uit_code(code),
                # eigen metadata, als vergelijkingsmateriaal
                "x_rd": None if pd.isna(rij.get("x_rd")) else float(rij["x_rd"]),
                "y_rd": None if pd.isna(rij.get("y_rd")) else float(rij["y_rd"]),
                "hoogte_maaiveld_nap": None if pd.isna(rij.get("hoogte_maaiveld_nap")) else float(rij["hoogte_maaiveld_nap"]),
                "bovenkant_filter_nap": None if pd.isna(rij.get("bovenkant_filter_nap")) else float(rij["bovenkant_filter_nap"]),
                "onderkant_filter_nap": None if pd.isna(rij.get("onderkant_filter_nap")) else float(rij["onderkant_filter_nap"]),
                "in_boxplot": bool(rij.get("in_boxplot", True)),
                # wat al handmatig is vastgelegd
                "meetreeks_id_werkboek": "" if pd.isna(rij.get("meetreeks_id")) else str(rij["meetreeks_id"]),
                "handmatig_vastgezet": bool(rij.get("handmatig_vastgezet", False)),
            }
        )

print(f"{len(profiel_rijen)} profielen, {len(peilbuis_rijen)} peilbuizen gelezen")

# COMMAND ----------

df_profielen = spark.createDataFrame(pd.DataFrame(profiel_rijen)).withColumn(
    "geladen_op", F.current_timestamp()
)
df_peilbuizen = spark.createDataFrame(pd.DataFrame(peilbuis_rijen)).withColumn(
    "geladen_op", F.current_timestamp()
)

display(df_peilbuizen.select(
    "project", "profiel_id", "positie", "zone", "peilbuis_naam",
    "naam_canoniek", "put_canoniek", "filter_nummer", "filter_uit_code_m",
))

# COMMAND ----------

# MAGIC %md
# MAGIC ## Controles op onze eigen kant
# MAGIC
# MAGIC Voordat er met het platform vergeleken wordt: is onze invoer compleet?
# MAGIC Een peilbuis zonder coordinaten kan niet ruimtelijk gematcht worden, en
# MAGIC een lege zone betekent dat de freatische lijn een anker mist.

# COMMAND ----------

display(
    df_peilbuizen.groupBy("project").agg(
        F.count("*").alias("peilbuizen"),
        F.sum(F.when(F.col("zone") == "", 1).otherwise(0)).alias("zone_leeg"),
        F.sum(F.when(F.col("x_rd").isNull(), 1).otherwise(0)).alias("geen_coordinaat"),
        F.sum(F.when(F.col("bovenkant_filter_nap").isNull(), 1).otherwise(0)).alias("geen_filterdiepte"),
        F.sum(F.when(F.col("meetreeks_id_werkboek") != "", 1).otherwise(0)).alias("heeft_meetreeks_id"),
        F.countDistinct("naam_canoniek").alias("unieke_namen"),
    ).orderBy("project")
)

# COMMAND ----------

# MAGIC %md
# MAGIC Dezelfde peilbuisnaam kan in meerdere projecten voorkomen -- de
# MAGIC metadatawerkboeken hergebruiken `HB001PB01` per onderzoeksproject. Dat is
# MAGIC precies waarom `projectnummer` meereist: zonder dat is de naam geen sleutel.

# COMMAND ----------

display(
    df_peilbuizen.groupBy("naam_canoniek")
    .agg(
        F.countDistinct("project").alias("aantal_projecten"),
        F.collect_set("project").alias("projecten"),
    )
    .filter(F.col("aantal_projecten") > 1)
    .orderBy(F.col("aantal_projecten").desc())
)

# COMMAND ----------

if SCHRIJVEN:
    for df, naam in ((df_profielen, "profielen"), (df_peilbuizen, "peilbuizen")):
        tabel: str = paths.uit_tabel(naam)
        df.write.mode("overwrite").option("overwriteSchema", "true").saveAsTable(tabel)
        print(f"  geschreven: {tabel} ({df.count()} rijen)")
else:
    print("schrijven=false, niets weggeschreven")
