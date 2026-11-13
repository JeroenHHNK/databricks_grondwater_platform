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
# MAGIC | `<prefix>_return_levels_chosen` (view) | the latest return levels that apply per picked tail: up to T=100 the empirical level (`bron='empirisch'`), past it the picked family at the picked threshold `n_kept` (`bron='familie'`; blank `n_kept` = the default threshold). A tail without a pick has none |
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

from tarso_pipeline.evt import EMPIRICAL_MAX_T  # noqa: E402
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
    WITH lv AS ({latest_run_sql(LEVELS_TABLE)}),
         c AS (SELECT * FROM {CHOICE_TABLE} WHERE family IN ('gpd', 'exp'))
    SELECT lv.*, c.family AS picked_family, c.n_kept AS picked_kept,
           CASE WHEN lv.family = 'emp' THEN 'empirisch' ELSE 'familie' END AS bron,
           c.note AS family_note, c.chosen_by, c.chosen_at
    FROM lv JOIN c
      ON lv.meetreeks_id = c.meetreeks_id AND lv.model_type = c.model_type AND lv.tail = c.tail
    WHERE (lv.family = 'emp' AND lv.return_period <= {EMPIRICAL_MAX_T})
       OR (lv.family = c.family AND lv.return_period > {EMPIRICAL_MAX_T}
           AND (CASE WHEN nullif(c.n_kept, '') IS NULL THEN lv.default_kept
                     ELSE lv.n_kept = CAST(c.n_kept AS INT) END))
""")
print(f"created {VIEW}")
display(spark.sql(f"""
    SELECT tail, picked_family, count(DISTINCT meetreeks_id) AS series
    FROM {VIEW} GROUP BY ALL ORDER BY ALL
"""))

# COMMAND ----------

# MAGIC %md
# MAGIC ## The model per series
# MAGIC
# MAGIC Every series is fitted with several model types; `run_extremes` picks one per series
# MAGIC (`<prefix>_model_selection.selected`) and a person can overrule that in the app
# MAGIC (*Pastas -> Modellen -> Modelvergelijking*), saved in git as
# MAGIC `pastas/model_choice/<prefix>_model_choice.csv`. This writes those picks to
# MAGIC `<prefix>_model_choice` (overwrite) and the view `<prefix>_model_in_use`: per series the
# MAGIC model that applies, the person's pick first, else the automatic one (`bron`).

# COMMAND ----------

from tarso_pipeline.selection import model_choice_path, read_model_choice  # noqa: E402

MODEL_CHOICE_TABLE = output_table(BASIS, "model_choice", CATALOG, SCHEMA)
MODEL_SELECTION_TABLE = output_table(BASIS, "model_selection", CATALOG, SCHEMA)
MODEL_IN_USE_VIEW = MODEL_SELECTION_TABLE.replace("_model_selection", "_model_in_use")
model_choice = read_model_choice(model_choice_path(ROOT, BASIS))
print(f"{len(model_choice)} model pick(s) in {model_choice_path(ROOT, BASIS)}")
(spark.createDataFrame(model_choice, schema=", ".join(f"{c} string" for c in model_choice.columns))
 .withColumn("loaded_at", F.current_timestamp())
 .write.format("delta").mode("overwrite").option("overwriteSchema", "true")
 .saveAsTable(MODEL_CHOICE_TABLE))
print(f"overwrote {MODEL_CHOICE_TABLE}")

if spark.catalog.tableExists(MODEL_SELECTION_TABLE):
    spark.sql(f"""
        CREATE OR REPLACE VIEW {MODEL_IN_USE_VIEW} AS
        WITH auto AS (SELECT meetreeks_id, model_type
                      FROM ({latest_run_sql(MODEL_SELECTION_TABLE, keys=("meetreeks_id",))}) WHERE selected)
        SELECT coalesce(c.meetreeks_id, a.meetreeks_id) AS meetreeks_id,
               coalesce(nullif(c.model_type, ''), a.model_type) AS model_type,
               CASE WHEN nullif(c.model_type, '') IS NOT NULL THEN 'handmatig' ELSE 'automatisch' END AS bron,
               c.note AS model_note, c.chosen_by, c.chosen_at
        FROM auto a FULL OUTER JOIN {MODEL_CHOICE_TABLE} c ON a.meetreeks_id = c.meetreeks_id
    """)
    print(f"created {MODEL_IN_USE_VIEW}")
else:
    print(f"No {MODEL_SELECTION_TABLE} yet: run run_extremes.py (select_models=true) first.")
