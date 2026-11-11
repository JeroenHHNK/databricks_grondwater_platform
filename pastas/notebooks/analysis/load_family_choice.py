# Databricks notebook source
# MAGIC %md
# MAGIC # Load the chosen statistical families into Delta
# MAGIC
# MAGIC **Run by hand after pulling the Git folder.** In the Streamlit app (*Pastas → Modellen*,
# MAGIC run locally where saving is on) a person picks the family per model and tail: GEV,
# MAGIC exponential (Gumbel), GPD or empirical. The picks are saved in git as
# MAGIC `pastas/family_choice/<prefix>_family_choice.csv`, the same route as the data
# MAGIC validation review. This notebook writes them to Delta:
# MAGIC
# MAGIC | object | what |
# MAGIC |---|---|
# MAGIC | `<prefix>_family_choice` | the picks, one row per series x model type x tail (overwrite) |
# MAGIC | `<prefix>_return_levels_chosen` (view) | the latest return levels in the family that applies: the pick, or else the automatic `chosen_family` of `<prefix>_evt_selection`; `family_source` says which |
# MAGIC
# MAGIC Boxplots draws the same levels per well in the cross sections.

# COMMAND ----------

dbutils.widgets.dropdown("data_basis", "unvalidated", ["unvalidated", "validated"], "Families of")
dbutils.widgets.text("output_catalog", "waterkeringbeheer_prd", "Output catalog")
dbutils.widgets.text("output_schema", "jvanruitenbeek", "Output schema")

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
            raise RuntimeError(f"No pyproject.toml above {os.getcwd()}; run this from the Git folder.")
        current = parent


ROOT = _repo_root()
if os.path.join(ROOT, "src") not in sys.path:
    sys.path.insert(0, os.path.join(ROOT, "src"))

from pyspark.sql import functions as F  # noqa: E402

from tarso_pipeline.paths import output_table  # noqa: E402
from tarso_pipeline.results import family_choice_path, latest_run_sql, read_family_choice  # noqa: E402

BASIS = dbutils.widgets.get("data_basis")
CATALOG = dbutils.widgets.get("output_catalog").strip()
SCHEMA = dbutils.widgets.get("output_schema").strip()
CHOICE_TABLE = output_table(BASIS, "family_choice", CATALOG, SCHEMA)
LEVELS_TABLE = output_table(BASIS, "return_levels", CATALOG, SCHEMA)
SELECTION_TABLE = output_table(BASIS, "evt_selection", CATALOG, SCHEMA)
VIEW = f"{LEVELS_TABLE}_chosen"

choice = read_family_choice(family_choice_path(ROOT, BASIS))
print(f"{len(choice)} pick(s) in {family_choice_path(ROOT, BASIS)}")
display(choice)

# COMMAND ----------

schema = ", ".join(f"{c} string" for c in choice.columns)
(spark.createDataFrame(choice, schema=schema)
 .withColumn("loaded_at", F.current_timestamp())
 .write.format("delta").mode("overwrite").option("overwriteSchema", "true")
 .saveAsTable(CHOICE_TABLE))
print(f"overwrote {CHOICE_TABLE}")

# COMMAND ----------

if not (spark.catalog.tableExists(LEVELS_TABLE) and spark.catalog.tableExists(SELECTION_TABLE)):
    dbutils.notebook.exit(f"No {LEVELS_TABLE} / {SELECTION_TABLE} yet: run run_extremes.py for this basis first.")

spark.sql(f"""
    CREATE OR REPLACE VIEW {VIEW} AS
    WITH lv AS ({latest_run_sql(LEVELS_TABLE)}),
         sel AS ({latest_run_sql(SELECTION_TABLE)}),
         fam AS (
             SELECT s.meetreeks_id, s.model_type, s.tail,
                    coalesce(nullif(c.family, ''), s.chosen_family) AS family,
                    CASE WHEN nullif(c.family, '') IS NOT NULL THEN 'gekozen' ELSE 'automatisch' END
                        AS family_source
             FROM sel s
             LEFT JOIN {CHOICE_TABLE} c
               ON c.meetreeks_id = s.meetreeks_id AND c.model_type = s.model_type AND c.tail = s.tail
         )
    SELECT lv.*, fam.family_source
    FROM lv JOIN fam
      ON lv.meetreeks_id = fam.meetreeks_id AND lv.model_type = fam.model_type
     AND lv.tail = fam.tail AND lv.family = fam.family
""")
print(f"created {VIEW}")
display(spark.sql(f"""
    SELECT family_source, tail, family, count(DISTINCT meetreeks_id) AS series
    FROM {VIEW} GROUP BY ALL ORDER BY ALL
"""))
