# Databricks notebook source
# MAGIC %md
# MAGIC # Load the chosen statistical families into Delta
# MAGIC
# MAGIC **Run by hand after pulling the Git folder.** In the Streamlit app (*Pastas → Familie kiezen*,
# MAGIC run locally where saving is on) a person picks the family per model and tail: GEV,
# MAGIC exponential (Gumbel), GPD or empirical. The picks are saved in git as
# MAGIC `pastas/family_choice/<prefix>_family_choice.csv`, the same route as the data
# MAGIC validation review. This notebook writes them to Delta:
# MAGIC
# MAGIC | object | what |
# MAGIC |---|---|
# MAGIC | `<prefix>_family_choice` | the picks, one row per series x model type x tail (overwrite) |
# MAGIC | `<prefix>_return_levels_chosen` (view) | the latest return levels in the family picked per tail; a tail without a pick has none (the automatic `chosen_family` of `<prefix>_evt_selection` is not used) |
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

if not spark.catalog.tableExists(LEVELS_TABLE):
    dbutils.notebook.exit(f"No {LEVELS_TABLE} yet: run run_extremes.py for this basis first.")

spark.sql(f"""
    CREATE OR REPLACE VIEW {VIEW} AS
    WITH lv AS ({latest_run_sql(LEVELS_TABLE)})
    SELECT lv.*, c.note AS family_note, c.chosen_by, c.chosen_at
    FROM lv JOIN {CHOICE_TABLE} c
      ON lv.meetreeks_id = c.meetreeks_id AND lv.model_type = c.model_type
     AND lv.tail = c.tail AND lv.family = c.family
""")
print(f"created {VIEW}")
display(spark.sql(f"""
    SELECT tail, family, count(DISTINCT meetreeks_id) AS series
    FROM {VIEW} GROUP BY ALL ORDER BY ALL
"""))
