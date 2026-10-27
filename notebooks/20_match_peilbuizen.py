# Databricks notebook source
# MAGIC %md
# MAGIC # 20 - Kunnen we onze peilbuizen in het dataplatform vinden?
# MAGIC
# MAGIC Dit is het notebook waar de MVP om gaat. De vraag is niet "haal de data op"
# MAGIC maar **"bestaan onze peilbuizen daar, en onder welke sleutel?"**
# MAGIC
# MAGIC Wij kennen een peilbuis als `HB002PB01` met code
# MAGIC `B_BE0072+3_BUKR_GMW_PB1_F-385`. Het platform kent
# MAGIC `meetreeks.code = 'HB28-PB1 '` en
# MAGIC `sensor_meetlocatie_sleutel = '507080_HB28_GMW_PB1'`. Welke sleutel die twee
# MAGIC verbindt is **niet bekend**. Daarom draaien hier vijf strategieen *naast
# MAGIC elkaar*; ze worden samengevoegd, niet na elkaar afgebroken, en de uitkomst
# MAGIC is een trefkans per strategie.
# MAGIC
# MAGIC | # | strategie | grondslag |
# MAGIC |---|---|---|
# MAGIC | 1 | `naam_canoniek` | `HB002PB01` -> `HB2PB1` tegen `trim(code)` -> `HB2PB1` |
# MAGIC | 2 | `put_filter` | putdeel + filternummer los vergeleken |
# MAGIC | 3 | `sensor_sleutel` | tegen het middenstuk van `sensor_meetlocatie_sleutel` |
# MAGIC | 4 | `ruimtelijk` | dichtstbijzijnde `meetlocatie` in RD, <= `max_afstand_m` |
# MAGIC | 5 | `filterdiepte` | onderscheidt geneste filters in dezelfde put |
# MAGIC
# MAGIC Strategie 4 is de **scheidsrechter**: die kijkt niet naar namen. Strategie 5
# MAGIC is het enige dat PB1/PB2/PB3 in dezelfde put uit elkaar houdt.
# MAGIC
# MAGIC **Een lage trefkans is een geldige uitkomst.** Dan zijn onze peilbuizen niet
# MAGIC (onder deze sleutels) in deze dataset te vinden, en is een handmatige
# MAGIC koppeltabel het antwoord in plaats van een normalisatieregel.

# COMMAND ----------

dbutils.widgets.text("max_afstand_m", "25", "Max. afstand ruimtelijke match (m)")
dbutils.widgets.text("filter_tolerantie_m", "0.15", "Tolerantie filterdiepte (m)")
dbutils.widgets.dropdown("schrijven", "true", ["true", "false"], "Tabellen schrijven")
dbutils.widgets.text("compartiment", "", "Filter meetlocatie op compartiment_code (leeg = alles)")

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

from pyspark.sql import DataFrame, functions as F  # noqa: E402

from Modules import paths  # noqa: E402

MAX_AFSTAND: float = float(dbutils.widgets.get("max_afstand_m"))
FILTER_TOL: float = float(dbutils.widgets.get("filter_tolerantie_m"))
SCHRIJVEN: bool = dbutils.widgets.get("schrijven") == "true"
COMPARTIMENT: str = dbutils.widgets.get("compartiment").strip()

# COMMAND ----------

# MAGIC %md
# MAGIC ## Onze kant
# MAGIC
# MAGIC Uit notebook 10. Alleen peilbuizen die nog geen handmatig vastgezette
# MAGIC `meetreeks_id` hebben, hoeven gematcht te worden -- maar ze gaan wel mee in
# MAGIC de rapportage, zodat de dekkingspercentages over het geheel gaan.

# COMMAND ----------

onze: DataFrame = spark.table(paths.uit_tabel("peilbuizen"))
print(f"{onze.count()} peilbuizen uit {onze.select('project').distinct().count()} projecten")

# COMMAND ----------

# MAGIC %md
# MAGIC ## De platformkant
# MAGIC
# MAGIC `meetreeks` met, waar mogelijk, `meetlocatie` erbij. De brug wordt via drie
# MAGIC wegen gelegd: `meetlocatie_object_id`, `meetlocatie_code` en als terugval
# MAGIC `meetlocatie.code = trim(meetreeks.code)`. Eerst meten hoe vaak elk daarvan
# MAGIC iets oplevert.

# COMMAND ----------

# `canoniek` in SQL: hoofdletters, niet-alfanumeriek eruit, voorloopnullen per
# cijfergroep weg. Dat laatste kan regexp_replace niet in één keer, dus wordt het
# in twee stappen gedaan: eerst scheidingstekens weg, dan '0+' na een letter.
CANONIEK_SQL = r"""
regexp_replace(
    regexp_replace(upper(regexp_replace({kolom}, '[^A-Za-z0-9]+', '')), '(?<=[A-Z])0+([0-9])', '$1'),
    '^0+([0-9])', '$1'
)
"""

spark.sql(f"""
    CREATE OR REPLACE TEMP VIEW platform_reeksen AS
    SELECT
        r.id                                       AS meetreeks_id,
        trim(r.code)                               AS meetreeks_code,
        {CANONIEK_SQL.format(kolom='r.code')}      AS code_canoniek,
        r.eenheid,
        r.begin_filter,
        r.eind_filter,
        r.bovenkant_peilbuis,
        r.bronsysteem,
        r.sensor_meetlocatie_sleutel,
        -- middenstuk van '507080_HB28_GMW_PB1' -> 'HB28'
        {CANONIEK_SQL.format(kolom="regexp_extract(r.sensor_meetlocatie_sleutel, '^[^_]+_([^_]+)', 1)")}
                                                   AS sensor_put_canoniek,
        upper(regexp_extract(r.sensor_meetlocatie_sleutel, '(PB[0-9]+)', 1))
                                                   AS sensor_filter_nummer,
        r.meetlocatie_code,
        r.meetlocatie_object_id,
        l.code                                     AS locatie_code,
        l.naam                                     AS locatie_naam,
        l.compartiment_code,
        l.compartiment_naam,
        l.peilgebiedpraktijk_code,
        l.meetreeks_actief,
        CAST(l.rd_xcoordinaat AS DOUBLE)           AS locatie_x_rd,
        CAST(l.rd_ycoordinaat AS DOUBLE)           AS locatie_y_rd,
        CAST(l.wgs84_xcoordinaat AS DOUBLE)        AS locatie_lon,
        CAST(l.wgs84_ycoordinaat AS DOUBLE)        AS locatie_lat,
        CASE
            WHEN l_id.id   IS NOT NULL THEN 'object_id'
            WHEN l_code.id IS NOT NULL THEN 'locatie_code'
            WHEN l_naam.id IS NOT NULL THEN 'code_gelijk'
            ELSE 'geen'
        END                                        AS locatie_brug
    FROM {paths.TBL_MEETREEKS} r
    LEFT JOIN {paths.TBL_MEETLOCATIE} l_id   ON l_id.id   = r.meetlocatie_object_id
    LEFT JOIN {paths.TBL_MEETLOCATIE} l_code ON l_code.code = r.meetlocatie_code
    LEFT JOIN {paths.TBL_MEETLOCATIE} l_naam ON l_naam.code = trim(r.code)
    LEFT JOIN {paths.TBL_MEETLOCATIE} l
           ON l.id = coalesce(l_id.id, l_code.id, l_naam.id)
""")

display(
    spark.sql("""
        SELECT locatie_brug, count(*) AS reeksen,
               round(100.0 * count(*) / sum(count(*)) OVER (), 1) AS pct
        FROM platform_reeksen GROUP BY locatie_brug ORDER BY reeksen DESC
    """)
)

# COMMAND ----------

# MAGIC %md
# MAGIC > Als `geen` hier domineert, heeft het platform voor onze reeksen geen
# MAGIC > coordinaten en valt strategie 4 weg. Dat blokkeert de pipeline niet: de
# MAGIC > RD-coordinaten staan ook in ons eigen werkboek.

# COMMAND ----------

# MAGIC %md ### Welke vorm hebben de peilbuiscodes op het platform?
# MAGIC Handig om te zien of `HB..-PB..` de huisstijl is of een van vele.

# COMMAND ----------

display(
    spark.sql("""
        SELECT regexp_extract(meetreeks_code, '^([A-Za-z]+)', 1) AS voorvoegsel,
               count(*) AS reeksen,
               min(meetreeks_code) AS voorbeeld_min,
               max(meetreeks_code) AS voorbeeld_max
        FROM platform_reeksen
        GROUP BY voorvoegsel ORDER BY reeksen DESC LIMIT 30
    """)
)

# COMMAND ----------

platform: DataFrame = spark.table("platform_reeksen")
if COMPARTIMENT:
    voor: int = platform.count()
    platform = platform.filter(
        (F.col("compartiment_code") == COMPARTIMENT) | F.col("compartiment_code").isNull()
    )
    print(f"compartimentfilter '{COMPARTIMENT}': {voor} -> {platform.count()} reeksen")
    print("  (NULL-compartiment blijft staan: dat is 'onbekend', niet 'oppervlaktewater')")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Strategie 1-3: op naam
# MAGIC
# MAGIC Elke strategie levert kandidaatparen op met dezelfde kolommen, zodat ze met
# MAGIC `unionByName` op elkaar te stapelen zijn.

# COMMAND ----------

KANDIDAAT_KOLOMMEN: list[str] = [
    "project", "profiel_id", "positie", "peilbuis_naam",
    "meetreeks_id", "meetreeks_code", "strategie", "afstand_m", "filter_verschil_m",
]


def als_kandidaten(df: DataFrame, strategie: str) -> DataFrame:
    """Brengt een join-resultaat op de gemeenschappelijke kandidaatvorm."""
    for kolom in ("afstand_m", "filter_verschil_m"):
        if kolom not in df.columns:
            df = df.withColumn(kolom, F.lit(None).cast("double"))
    return df.withColumn("strategie", F.lit(strategie)).select(*KANDIDAAT_KOLOMMEN)


# 1. volledige canonieke naam tegen de peilbuiscode
s1 = als_kandidaten(
    onze.join(platform, onze["naam_canoniek"] == platform["code_canoniek"], "inner"),
    "naam_canoniek",
)

# 2. putdeel + filternummer los: vangt gevallen waar de code anders samengesteld is
s2 = als_kandidaten(
    onze.join(
        platform,
        (onze["put_canoniek"] == platform["sensor_put_canoniek"])
        & (onze["filter_nummer"] == platform["sensor_filter_nummer"])
        & (onze["filter_nummer"] != ""),
        "inner",
    ),
    "put_filter",
)

# 3. alleen het putdeel tegen het sensorsleutel-middenstuk (ruimer dan 2)
s3 = als_kandidaten(
    onze.join(
        platform,
        (onze["put_canoniek"] == platform["sensor_put_canoniek"])
        & (onze["put_canoniek"] != ""),
        "inner",
    ),
    "sensor_sleutel",
)

for naam, df in (("naam_canoniek", s1), ("put_filter", s2), ("sensor_sleutel", s3)):
    print(f"  {naam:<16} {df.count():>6} kandidaatparen")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Strategie 4: ruimtelijk - de scheidsrechter
# MAGIC
# MAGIC Onafhankelijk van elke naamgeving. Alleen mogelijk waar het platform een
# MAGIC coordinaat heeft (`locatie_brug != 'geen'`) en wij er een hebben.
# MAGIC Een cross join over een paar honderd peilbuizen maal de reeksen met
# MAGIC coordinaat is klein genoeg; de afstandsfilter gaat er direct over.

# COMMAND ----------

met_coord: DataFrame = platform.filter(
    F.col("locatie_x_rd").isNotNull() & F.col("locatie_y_rd").isNotNull()
)
onze_met_coord: DataFrame = onze.filter(F.col("x_rd").isNotNull() & F.col("y_rd").isNotNull())
print(f"{onze_met_coord.count()} peilbuizen met coordinaat, {met_coord.count()} reeksen met coordinaat")

afstand = F.sqrt(
    F.pow(onze_met_coord["x_rd"] - met_coord["locatie_x_rd"], 2)
    + F.pow(onze_met_coord["y_rd"] - met_coord["locatie_y_rd"], 2)
)

s4 = als_kandidaten(
    onze_met_coord.join(met_coord, afstand <= MAX_AFSTAND, "inner").withColumn(
        "afstand_m", F.round(afstand, 2)
    ),
    "ruimtelijk",
)
print(f"  ruimtelijk       {s4.count():>6} kandidaatparen binnen {MAX_AFSTAND} m")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Strategie 5: filterdiepte als onderscheider
# MAGIC
# MAGIC Binnen een ruimtelijke kandidatenset zitten de geneste filters van dezelfde
# MAGIC put op bijna hetzelfde punt. Alleen de filterdiepte houdt die uit elkaar:
# MAGIC onze `bovenkant_filter_nap` tegen `meetreeks.begin_filter`.

# COMMAND ----------

filter_verschil = F.abs(
    onze_met_coord["bovenkant_filter_nap"] - met_coord["begin_filter"].cast("double")
)

s5 = als_kandidaten(
    onze_met_coord.join(
        met_coord,
        (afstand <= MAX_AFSTAND)
        & onze_met_coord["bovenkant_filter_nap"].isNotNull()
        & met_coord["begin_filter"].isNotNull()
        & (filter_verschil <= FILTER_TOL),
        "inner",
    )
    .withColumn("afstand_m", F.round(afstand, 2))
    .withColumn("filter_verschil_m", F.round(filter_verschil, 3)),
    "ruimtelijk_filter",
)
print(f"  ruimtelijk_filter {s5.count():>5} kandidaatparen (<= {FILTER_TOL} m filterverschil)")

# COMMAND ----------

# MAGIC %md
# MAGIC ### Toets op de betekenis van het `_F-385`-achtervoegsel
# MAGIC
# MAGIC Onze codes eindigen op `_F-385`, vermoedelijk de filterdiepte in centimeters,
# MAGIC maar onbekend of dat m NAP is of diepte onder maaiveld. Dit meet beide in
# MAGIC plaats van er een aan te nemen.

# COMMAND ----------

display(
    onze.filter(F.col("filter_uit_code_m").isNotNull())
    .select(
        F.corr("filter_uit_code_m", "bovenkant_filter_nap").alias("corr_met_nap"),
        F.corr(
            "filter_uit_code_m",
            F.col("hoogte_maaiveld_nap") - F.col("bovenkant_filter_nap"),
        ).alias("corr_met_diepte_onder_maaiveld"),
        F.count("*").alias("n"),
    )
)

# COMMAND ----------

# MAGIC %md ## Alle kandidaten samen

# COMMAND ----------

kandidaten: DataFrame = (
    s1.unionByName(s2).unionByName(s3).unionByName(s4).unionByName(s5)
).distinct().cache()

print(f"{kandidaten.count()} kandidaatparen in totaal")
display(kandidaten.limit(50))

# COMMAND ----------

# MAGIC %md
# MAGIC ## Het rapport - de uitkomst van de MVP
# MAGIC
# MAGIC Trefkans per project per strategie: hoeveel van onze peilbuizen krijgt deze
# MAGIC strategie uberhaupt gekoppeld?

# COMMAND ----------

totaal_per_project: DataFrame = onze.groupBy("project").agg(
    F.count("*").alias("peilbuizen_totaal")
)

rapport: DataFrame = (
    kandidaten.groupBy("project", "strategie")
    .agg(
        F.countDistinct("peilbuis_naam").alias("gematcht"),
        F.count("*").alias("kandidaatparen"),
    )
    .join(totaal_per_project, "project")
    .withColumn(
        "trefkans_pct",
        F.round(100.0 * F.col("gematcht") / F.col("peilbuizen_totaal"), 1),
    )
    .orderBy("project", F.col("trefkans_pct").desc())
)
display(rapport)

# COMMAND ----------

# MAGIC %md ### Over alle projecten

# COMMAND ----------

display(
    kandidaten.groupBy("strategie")
    .agg(F.countDistinct("project", "peilbuis_naam").alias("gematchte_peilbuizen"))
    .withColumn(
        "van_totaal",
        F.lit(onze.count()),
    )
    .withColumn(
        "trefkans_pct",
        F.round(100.0 * F.col("gematchte_peilbuizen") / F.col("van_totaal"), 1),
    )
    .orderBy(F.col("trefkans_pct").desc())
)

# COMMAND ----------

# MAGIC %md
# MAGIC ### Dubbelzinnig en tegenstrijdig
# MAGIC
# MAGIC Interessanter dan de trefkans: waar wijzen twee strategieen **verschillende**
# MAGIC reeksen aan? Dat betekent dat de naamgeving en de geometrie het niet eens
# MAGIC zijn, en dat is geen detail om weg te ronden.

# COMMAND ----------

per_peilbuis: DataFrame = kandidaten.groupBy(
    "project", "profiel_id", "positie", "peilbuis_naam"
).agg(
    F.countDistinct("meetreeks_id").alias("n_kandidaten"),
    F.collect_set("strategie").alias("strategieen"),
    F.min("afstand_m").alias("min_afstand_m"),
)

display(
    per_peilbuis.withColumn(
        "status",
        F.when(F.col("n_kandidaten") == 1, "eenduidig")
        .when(F.col("n_kandidaten") > 1, "dubbelzinnig")
        .otherwise("geen"),
    )
    .groupBy("project", "status")
    .agg(F.count("*").alias("peilbuizen"))
    .orderBy("project", "status")
)

# COMMAND ----------

# MAGIC %md
# MAGIC Peilbuizen waar naam en geometrie elkaar tegenspreken: de naamstrategie
# MAGIC vindt reeks A, de ruimtelijke strategie reeks B.

# COMMAND ----------

op_naam = kandidaten.filter(F.col("strategie") == "naam_canoniek").select(
    "project", "peilbuis_naam", F.col("meetreeks_id").alias("id_op_naam")
)
op_plek = kandidaten.filter(F.col("strategie").startswith("ruimtelijk")).select(
    "project", "peilbuis_naam", F.col("meetreeks_id").alias("id_op_plek"), "afstand_m"
)
display(
    op_naam.join(op_plek, ["project", "peilbuis_naam"], "inner")
    .filter(F.col("id_op_naam") != F.col("id_op_plek"))
    .orderBy("project", "peilbuis_naam")
)

# COMMAND ----------

# MAGIC %md ### Niet gevonden - deze gaan naar een mens

# COMMAND ----------

niet_gevonden: DataFrame = onze.join(
    kandidaten.select("project", "peilbuis_naam").distinct(),
    ["project", "peilbuis_naam"],
    "left_anti",
)
print(f"{niet_gevonden.count()} van {onze.count()} peilbuizen zonder enige kandidaat")
display(
    niet_gevonden.select(
        "project", "profiel_id", "positie", "peilbuis_naam", "code_peilbuis",
        "naam_canoniek", "x_rd", "y_rd", "bovenkant_filter_nap",
    ).orderBy("project", "profiel_id", "positie")
)

# COMMAND ----------

# MAGIC %md
# MAGIC ## Voorstel per peilbuis, en de terugkoppeling naar het werkboek
# MAGIC
# MAGIC Voorkeursorde: een handmatig vastgezette keuze wint altijd; daarna
# MAGIC ruimtelijk+filter (naam en plek eens), dan ruimtelijk, dan de naamstrategieen.
# MAGIC
# MAGIC De export heeft **dezelfde kolomnamen als het blad `peilbuizen`**, zodat een
# MAGIC kolom overnemen in het werkboek kopieren-en-plakken is.

# COMMAND ----------

VOORKEUR: list[str] = [
    "ruimtelijk_filter", "ruimtelijk", "naam_canoniek", "put_filter", "sensor_sleutel",
]
rang = F.create_map(
    *[x for i, s in enumerate(VOORKEUR) for x in (F.lit(s), F.lit(i))]
)

from pyspark.sql import Window  # noqa: E402

venster = Window.partitionBy("project", "peilbuis_naam").orderBy(
    rang[F.col("strategie")].asc(), F.col("afstand_m").asc_nulls_last()
)

voorstel: DataFrame = (
    kandidaten.withColumn("rang", F.row_number().over(venster))
    .filter(F.col("rang") == 1)
    .drop("rang")
    .join(per_peilbuis.select("project", "peilbuis_naam", "n_kandidaten", "strategieen"),
          ["project", "peilbuis_naam"], "left")
    .withColumn(
        "status",
        F.when(F.col("n_kandidaten") > 1, "dubbelzinnig").otherwise("voorstel"),
    )
)

eind: DataFrame = (
    onze.select(
        "project", "profiel_id", "positie", "peilbuis_naam", "code_peilbuis",
        "meetreeks_id_werkboek", "handmatig_vastgezet",
    )
    .join(
        voorstel.select(
            "project", "peilbuis_naam",
            F.col("meetreeks_id").alias("voorstel_meetreeks_id"),
            F.col("meetreeks_code").alias("voorstel_meetreeks_code"),
            F.col("strategie").alias("match_methode"),
            F.col("afstand_m").alias("match_afstand_m"),
            "n_kandidaten", "strategieen", "status",
        ),
        ["project", "peilbuis_naam"],
        "left",
    )
    .withColumn("status", F.coalesce(F.col("status"), F.lit("geen_kandidaat")))
    # handmatig vastgezet overschrijven we nooit
    .withColumn(
        "meetreeks_id",
        F.when(F.col("handmatig_vastgezet"), F.col("meetreeks_id_werkboek")).otherwise(
            F.col("voorstel_meetreeks_id")
        ),
    )
    .withColumn(
        "match_methode",
        F.when(F.col("handmatig_vastgezet"), F.lit("handmatig")).otherwise(F.col("match_methode")),
    )
    .withColumn("match_datum", F.current_timestamp())
    .orderBy("project", "profiel_id", "positie")
)
display(eind)

# COMMAND ----------

if SCHRIJVEN:
    for df, naam in ((kandidaten, "match_kandidaten"), (rapport, "match_rapport"), (eind, "peilbuis_meetreeks")):
        tabel: str = paths.uit_tabel(naam)
        df.write.mode("overwrite").option("overwriteSchema", "true").saveAsTable(tabel)
        print(f"  geschreven: {tabel}")
else:
    print("schrijven=false, niets weggeschreven")

# COMMAND ----------

# MAGIC %md
# MAGIC ### Export voor de terugkoppeling
# MAGIC
# MAGIC Een csv per project op het volume. Werkwijze: downloaden, de geaccepteerde
# MAGIC meetlocatie als `meetlocatie_id` in `input_data/config.csv` zetten,
# MAGIC committen, pushen, de Git folder pullen en notebook 10 opnieuw draaien.
# MAGIC Lokaal doet `scripts/bouw_koppeltabel_ld_dd.py --vul-config` hetzelfde.
# MAGIC
# MAGIC Lukt dit niet, dan ontbreekt `WRITE VOLUME` (zie notebook 00) -- de tabel
# MAGIC `boxplots_peilbuis_meetreeks` hierboven bevat dezelfde gegevens.

# COMMAND ----------

from datetime import datetime, timezone  # noqa: E402

run_id: str = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
try:
    import os

    pandas_eind = eind.toPandas()
    map_pad: str = paths.volume_pad(f"match_{run_id}")
    os.makedirs(map_pad, exist_ok=True)
    for project, groep in pandas_eind.groupby("project"):
        doel: str = f"{map_pad}/{project}_match.csv"
        groep.to_csv(doel, index=False)
        print(f"  {doel}  ({len(groep)} rijen)")
except Exception as fout:  # noqa: BLE001
    print(f"  export mislukt: {type(fout).__name__}: {fout}")
    print("  Gebruik de tabel boxplots_peilbuis_meetreeks in plaats daarvan.")

# COMMAND ----------

# MAGIC %md
# MAGIC ---
# MAGIC ## Hoe je dit leest
# MAGIC
# MAGIC - **Trefkans > ~80% op `naam_canoniek`, ruimtelijk bevestigd** -> de sleutel is
# MAGIC   de genormaliseerde peilbuisnaam. Neem de voorstellen over en ga door.
# MAGIC - **Ruimtelijk hoog, op naam laag** -> de peilbuizen staan er wel, maar onder
# MAGIC   een andere naam. De ruimtelijke match is dan de koppeling, eenmalig
# MAGIC   vastgelegd in het werkboek.
# MAGIC - **Alles laag** -> onze peilbuizen zitten niet in deze dataset, of onder een
# MAGIC   sleutel die we nog niet gezien hebben. Dat is een uitkomst, geen fout:
# MAGIC   bespreek het met de beheerder van `hl_grondwaterpeilmeting` voordat er
# MAGIC   verder gebouwd wordt.
# MAGIC - **Tegenstrijdigheden** wegen zwaarder dan de trefkans. Een peilbuis die op
# MAGIC   naam reeks A en op plek reeks B oplevert, moet een mens beslissen.
