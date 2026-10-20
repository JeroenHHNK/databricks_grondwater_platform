# Databricks notebook source
# MAGIC %md
# MAGIC # 30 - Stijghoogtes en coordinaten per dwarsprofiel
# MAGIC
# MAGIC Haalt op, voor de peilbuizen die notebook 20 aan een `meetreeks_id` heeft
# MAGIC gekoppeld:
# MAGIC
# MAGIC 1. **stijghoogtes** uit `meting`, als dagwaarden per peilbuis;
# MAGIC 2. **coordinaten en filterdieptes** uit `meetreeks` + `meetlocatie`, naast
# MAGIC    onze eigen waarden, met het verschil erbij;
# MAGIC 3. een **dekkingstabel**: of het platform de Excel-export uberhaupt kan
# MAGIC    vervangen. Een peilbuis met drie maanden data vervangt geen reeks van
# MAGIC    drie jaar.
# MAGIC
# MAGIC De boxplots zelf blijven lokaal. Dit notebook levert de invoer daarvoor op:
# MAGIC een tabel plus, als het volume schrijfbaar is, een parquet/csv per project.
# MAGIC
# MAGIC **Tijdstempels:** geindexeerd op `tijdstempel_utc`, niet op `tijdstempel`.
# MAGIC Die laatste is lokale tijd en herhaalt elk jaar in oktober een uur, dus
# MAGIC dubbele en dubbelzinnige tijdstempels op de DST-grens.

# COMMAND ----------

dbutils.widgets.text("projecten", "", "Projecten (komma-gescheiden, leeg = alle)")
dbutils.widgets.text("vanaf_datum", "2020-01-01", "Vanaf datum")
dbutils.widgets.dropdown("aggregatie", "dag", ["dag", "ruw"], "Tijdstap")
dbutils.widgets.dropdown("schrijven", "true", ["true", "false"], "Tabellen schrijven")
dbutils.widgets.dropdown("exporteer_bestanden", "true", ["true", "false"], "Bestanden naar volume")

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

from datetime import datetime, timezone  # noqa: E402

from pyspark.sql import DataFrame, functions as F  # noqa: E402

from Modules import paths  # noqa: E402

GEVRAAGD: str = dbutils.widgets.get("projecten").strip()
VANAF: str = dbutils.widgets.get("vanaf_datum").strip()
AGGREGATIE: str = dbutils.widgets.get("aggregatie")
SCHRIJVEN: bool = dbutils.widgets.get("schrijven") == "true"
EXPORTEER: bool = dbutils.widgets.get("exporteer_bestanden") == "true"
RUN_ID: str = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

print(f"run_id {RUN_ID} | vanaf {VANAF} | aggregatie {AGGREGATIE}")

# COMMAND ----------

# MAGIC %md ## De gekoppelde peilbuizen

# COMMAND ----------

koppeling: DataFrame = spark.table(paths.uit_tabel("peilbuis_meetreeks")).filter(
    F.col("meetreeks_id").isNotNull() & (F.col("meetreeks_id") != "")
)
if GEVRAAGD:
    projecten: list[str] = [p.strip() for p in GEVRAAGD.split(",") if p.strip()]
    koppeling = koppeling.filter(F.col("project").isin(projecten))

peilbuizen: DataFrame = spark.table(paths.uit_tabel("peilbuizen"))
profielen: DataFrame = spark.table(paths.uit_tabel("profielen"))

print(f"{koppeling.count()} gekoppelde peilbuizen")
if koppeling.count() == 0:
    raise SystemExit(
        "Geen enkele peilbuis heeft een meetreeks_id. Draai eerst notebook 20 en "
        "neem de voorstellen over in de werkboeken."
    )

display(koppeling.groupBy("project", "match_methode").count().orderBy("project"))

# COMMAND ----------

# MAGIC %md
# MAGIC ## 1. Stijghoogtes
# MAGIC
# MAGIC Eerst controleren dat de eenheid is wat we denken. `waarde` zonder bekende
# MAGIC eenheid is geen stijghoogte.

# COMMAND ----------

ids: DataFrame = koppeling.select("meetreeks_id").distinct()

display(
    spark.table(paths.TBL_MEETREEKS)
    .join(ids, spark.table(paths.TBL_MEETREEKS)["id"] == ids["meetreeks_id"], "inner")
    .groupBy("eenheid")
    .agg(F.count("*").alias("reeksen"))
)

# MAGIC %md > Verwacht: alles `m NAP`. Iets anders betekent dat de waarden niet
# MAGIC > zonder omrekening in een boxplot van meters NAP horen.

# COMMAND ----------

metingen_ruw: DataFrame = (
    spark.table(paths.TBL_METING)
    .join(ids, F.col("meetreeks_id") == ids["meetreeks_id"], "inner")
    .drop(ids["meetreeks_id"])
    .filter(F.col("waarde").isNotNull())
    .filter(F.col("tijdstempel_utc") >= F.lit(VANAF).cast("timestamp"))
    # meting.id is niet uniek (open datakwaliteitsmelding op de bron), dus
    # ontdubbelen op het paar dat wel betekenis heeft.
    .dropDuplicates(["meetreeks_id", "tijdstempel_utc"])
)

print(f"{metingen_ruw.count():,} metingen na ontdubbelen")

# COMMAND ----------

if AGGREGATIE == "dag":
    metingen: DataFrame = (
        metingen_ruw.withColumn("datum", F.to_date("tijdstempel_utc"))
        .groupBy("meetreeks_id", "datum")
        .agg(
            F.round(F.avg("waarde"), 4).alias("stijghoogte_m_nap"),
            F.min("waarde").alias("min_op_dag"),
            F.max("waarde").alias("max_op_dag"),
            F.count("*").alias("n_metingen_op_dag"),
        )
    )
else:
    metingen = metingen_ruw.select(
        "meetreeks_id",
        F.col("tijdstempel_utc").alias("tijdstempel"),
        F.col("waarde").alias("stijghoogte_m_nap"),
        "validatie_status",
    )

reeksen: DataFrame = koppeling.select(
    "project", "profiel_id", "positie", "peilbuis_naam", "meetreeks_id"
).join(metingen, "meetreeks_id", "inner")

display(reeksen.limit(50))

# COMMAND ----------

# MAGIC %md
# MAGIC ## 2. Dekking - vervangt dit de Excel-export?
# MAGIC
# MAGIC De vraag die beslist of de pipeline over kan: bereik, aantal dagen en het
# MAGIC grootste gat per peilbuis.

# COMMAND ----------

tijdkolom: str = "datum" if AGGREGATIE == "dag" else "tijdstempel"

from pyspark.sql import Window  # noqa: E402

venster = Window.partitionBy("meetreeks_id").orderBy(tijdkolom)
met_gat: DataFrame = reeksen.withColumn(
    "gat_dagen",
    F.datediff(F.col(tijdkolom), F.lag(tijdkolom).over(venster)),
)

dekking: DataFrame = (
    met_gat.groupBy("project", "profiel_id", "positie", "peilbuis_naam", "meetreeks_id")
    .agg(
        F.count("*").alias("n_waarnemingen"),
        F.min(tijdkolom).alias("eerste"),
        F.max(tijdkolom).alias("laatste"),
        F.round(F.min("stijghoogte_m_nap"), 3).alias("min_m_nap"),
        F.round(F.max("stijghoogte_m_nap"), 3).alias("max_m_nap"),
        F.max("gat_dagen").alias("grootste_gat_dagen"),
    )
    .withColumn("spanwijdte_dagen", F.datediff("laatste", "eerste"))
    .orderBy("project", "profiel_id", "positie")
)
display(dekking)

# COMMAND ----------

display(
    dekking.groupBy("project").agg(
        F.count("*").alias("peilbuizen"),
        F.min("eerste").alias("vroegste"),
        F.max("laatste").alias("laatste"),
        F.round(F.avg("spanwijdte_dagen")).alias("gem_spanwijdte_dagen"),
        F.sum(F.when(F.col("spanwijdte_dagen") < 365, 1).otherwise(0)).alias("korter_dan_jaar"),
        F.max("grootste_gat_dagen").alias("grootste_gat_dagen"),
    ).orderBy("project")
)

# COMMAND ----------

# MAGIC %md
# MAGIC ## 3. Coordinaten en filterdieptes per dwarsprofiel
# MAGIC
# MAGIC Het platform ernaast, niet in plaats van. Onze eigen waarden blijven
# MAGIC leidend; de verschilkolommen laten zien of ze uit elkaar lopen. Zou het
# MAGIC platform de autoriteit worden, dan is de koppeling via een hash net zo
# MAGIC kwetsbaar als het rij-indexlijstje dat we net kwijt zijn.
# MAGIC
# MAGIC De beheerdersnamen uit `meetlocatie` (`gebiedsbeheerder_naam`,
# MAGIC `objectbeheerder`, `eigenaar`) gaan hier **bewust niet** mee: dat zijn
# MAGIC persoonsnamen en die horen niet in een export.

# COMMAND ----------

mr = spark.table(paths.TBL_MEETREEKS).alias("mr")
ml = spark.table(paths.TBL_MEETLOCATIE).alias("ml")

platform_locatie: DataFrame = (
    mr.join(ids, F.col("mr.id") == ids["meetreeks_id"], "inner")
    .join(
        ml,
        (F.col("ml.id") == F.col("mr.meetlocatie_object_id"))
        | (F.col("ml.code") == F.col("mr.meetlocatie_code"))
        | (F.col("ml.code") == F.trim(F.col("mr.code"))),
        "left",
    )
    .select(
        F.col("mr.id").alias("meetreeks_id"),
        F.trim(F.col("mr.code")).alias("meetreeks_code"),
        F.col("mr.eenheid"),
        F.col("mr.begin_filter").cast("double").alias("platform_bovenkant_filter"),
        F.col("mr.eind_filter").cast("double").alias("platform_onderkant_filter"),
        F.col("mr.bovenkant_peilbuis").cast("double").alias("platform_bovenkant_peilbuis"),
        F.col("mr.bronsysteem"),
        F.col("ml.code").alias("locatie_code"),
        F.col("ml.naam").alias("locatie_naam"),
        F.col("ml.compartiment_naam"),
        F.col("ml.peilgebiedpraktijk_code"),
        F.col("ml.meetreeks_actief"),
        F.col("ml.rd_xcoordinaat").cast("double").alias("platform_x_rd"),
        F.col("ml.rd_ycoordinaat").cast("double").alias("platform_y_rd"),
        F.col("ml.wgs84_xcoordinaat").cast("double").alias("platform_lon"),
        F.col("ml.wgs84_ycoordinaat").cast("double").alias("platform_lat"),
    )
    .dropDuplicates(["meetreeks_id"])
)

locaties: DataFrame = (
    koppeling.select("project", "profiel_id", "positie", "peilbuis_naam", "meetreeks_id")
    .join(
        peilbuizen.select(
            "project", "peilbuis_naam", "zone",
            F.col("x_rd").alias("eigen_x_rd"),
            F.col("y_rd").alias("eigen_y_rd"),
            F.col("bovenkant_filter_nap").alias("eigen_bovenkant_filter"),
            F.col("onderkant_filter_nap").alias("eigen_onderkant_filter"),
            F.col("hoogte_maaiveld_nap"),
        ),
        ["project", "peilbuis_naam"],
        "left",
    )
    .join(platform_locatie, "meetreeks_id", "left")
    .join(
        profielen.select(
            "project", "profiel_id",
            F.col("x_rd").alias("profiel_x_rd"),
            F.col("y_rd").alias("profiel_y_rd"),
            F.col("stix_bestand"),
        ),
        ["project", "profiel_id"],
        "left",
    )
    .withColumn(
        "afwijking_coord_m",
        F.round(
            F.sqrt(
                F.pow(F.col("eigen_x_rd") - F.col("platform_x_rd"), 2)
                + F.pow(F.col("eigen_y_rd") - F.col("platform_y_rd"), 2)
            ),
            2,
        ),
    )
    .withColumn(
        "afwijking_filter_m",
        F.round(F.abs(F.col("eigen_bovenkant_filter") - F.col("platform_bovenkant_filter")), 3),
    )
    .orderBy("project", "profiel_id", "positie")
)
display(locaties)

# COMMAND ----------

# MAGIC %md ### Waar lopen onze waarden en het platform uit elkaar?

# COMMAND ----------

display(
    locaties.groupBy("project").agg(
        F.count("*").alias("peilbuizen"),
        F.sum(F.when(F.col("platform_x_rd").isNull(), 1).otherwise(0)).alias("geen_platform_coord"),
        F.round(F.max("afwijking_coord_m"), 1).alias("max_afwijking_coord_m"),
        F.round(F.avg("afwijking_coord_m"), 1).alias("gem_afwijking_coord_m"),
        F.round(F.max("afwijking_filter_m"), 2).alias("max_afwijking_filter_m"),
        F.sum(F.when(F.col("meetreeks_actief") == "Nee", 1).otherwise(0)).alias("locatie_inactief"),
    ).orderBy("project")
)

# COMMAND ----------

if SCHRIJVEN:
    doelen = (
        (reeksen.withColumn("run_id", F.lit(RUN_ID)), "stijghoogtes", "append"),
        (dekking.withColumn("run_id", F.lit(RUN_ID)), "stijghoogte_dekking", "overwrite"),
        (locaties.withColumn("run_id", F.lit(RUN_ID)), "peilbuis_locaties", "overwrite"),
    )
    for df, naam, modus in doelen:
        tabel: str = paths.uit_tabel(naam)
        schrijver = df.write.mode(modus)
        if modus == "overwrite":
            schrijver = schrijver.option("overwriteSchema", "true")
        schrijver.saveAsTable(tabel)
        print(f"  {modus:<9} {tabel}")
else:
    print("schrijven=false, niets weggeschreven")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Export naar het volume
# MAGIC
# MAGIC Dit is in de praktijk de koppeling met de lokale pipeline: die heeft geen
# MAGIC Spark, dus moet iemand het resultaat kunnen downloaden. Parquet voor de
# MAGIC pipeline, csv om snel in te kijken.

# COMMAND ----------

if EXPORTEER:
    import os

    try:
        map_pad: str = paths.volume_pad(f"export_{RUN_ID}")
        os.makedirs(map_pad, exist_ok=True)
        pd_reeksen = reeksen.toPandas()
        pd_locaties = locaties.toPandas()
        pd_dekking = dekking.toPandas()
        for project in sorted(pd_reeksen["project"].unique()):
            pd_reeksen[pd_reeksen["project"] == project].to_parquet(
                f"{map_pad}/{project}_stijghoogtes.parquet", index=False
            )
            pd_locaties[pd_locaties["project"] == project].to_csv(
                f"{map_pad}/{project}_locaties.csv", index=False
            )
            print(f"  {project}")
        pd_dekking.to_csv(f"{map_pad}/dekking.csv", index=False)
        print(f"\nklaar: {map_pad}")
    except Exception as fout:  # noqa: BLE001
        print(f"  export mislukt: {type(fout).__name__}: {fout}")
        print("  Zonder WRITE VOLUME blijven de Delta-tabellen de uitvoer.")
else:
    print("exporteer_bestanden=false")

# COMMAND ----------

# MAGIC %md
# MAGIC ---
# MAGIC **Klaar als:** `dekking` voor de peilbuizen van een project een bereik laat
# MAGIC zien dat vergelijkbaar is met de `peilbuisdata_*.xlsx`-export, en
# MAGIC `afwijking_coord_m` klein is. Controleer een paar peilbuizen met de hand
# MAGIC tegen die export voordat je hem met pensioen stuurt.
