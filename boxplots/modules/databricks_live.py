# databricks_live.py
"""
Stijghoogtes rechtstreeks uit het datawarehouse, via een SQL warehouse.

Dezelfde gegevens als `notebooks/db_notebook_exporteer_stijghoogtes.py` naar
`input_data/databricks_export/` schrijft, maar op het moment van vragen. De app
roept dit één keer per sessie aan; zie `databricks_reeksen.bron()`.

Inloggen gaat via `databricks.sdk.core.Config`:
- in een Databricks App met de service principal van de app (Databricks zet
  `DATABRICKS_HOST`, `DATABRICKS_CLIENT_ID` en `DATABRICKS_CLIENT_SECRET`);
- lokaal met een profiel uit `~/.databrickscfg` of `DATABRICKS_HOST` + token.

Welk warehouse: `DATABRICKS_WAREHOUSE_ID`. In de app komt die uit de resource
`sql-warehouse` (zie `app.yaml`). Zonder die variabele is er geen live bron.

De tabellen en kolommen staan in DATA_CATALOG.md.
"""

from __future__ import annotations

import os
import re

import pandas as pd
from pandas import DataFrame

from . import paths

WAREHOUSE_VAR = "DATABRICKS_WAREHOUSE_ID"

# Een meetlocatie_id is een sha256 in hex. Alleen zulke waarden gaan de query in.
_ID = re.compile(r"[0-9a-f]{64}")

# Meetreeks -> meetlocatie: via meetlocatie_object_id, dan meetlocatie_code,
# dan de code van de reeks zelf; dezelfde volgorde als in de db_notebooks.
_REEKS_LOCATIE = f"""
    reeks_locatie AS (
        SELECT r.id AS meetreeks_id, trim(r.code) AS meetreeks_code, r.eenheid,
               CAST(r.begin_filter AS DOUBLE) AS begin_filter,
               CAST(r.eind_filter AS DOUBLE) AS eind_filter,
               CAST(r.bovenkant_peilbuis AS DOUBLE) AS bovenkant_peilbuis,
               coalesce(l_id.id, l_code.id, l_naam.id) AS meetlocatie_id
        FROM {paths.TBL_MEETREEKS} r
        LEFT JOIN {paths.TBL_MEETLOCATIE} l_id   ON l_id.id     = r.meetlocatie_object_id
        LEFT JOIN {paths.TBL_MEETLOCATIE} l_code ON l_code.code = r.meetlocatie_code
        LEFT JOIN {paths.TBL_MEETLOCATIE} l_naam ON l_naam.code = trim(r.code)
    )"""


def beschikbaar() -> bool:
    """Is er een warehouse ingesteld? Zegt niets over rechten of bereikbaarheid."""
    return bool(os.environ.get(WAREHOUSE_VAR, "").strip())


def _ids_sql(meetlocatie_ids: list[str]) -> str:
    ids = sorted({i for i in meetlocatie_ids if isinstance(i, str) and _ID.fullmatch(i)})
    if not ids:
        raise ValueError("Geen geldige meetlocatie_id om op te vragen.")
    return ", ".join(f"'{i}'" for i in ids)


def _query(cursor, sql: str) -> DataFrame:
    cursor.execute(sql)
    return cursor.fetchall_arrow().to_pandas()


def haal_op(meetlocatie_ids: list[str]) -> tuple[DataFrame, DataFrame]:
    """
    De meetreeksen van deze meetlocaties en hun daggemiddelden.

    Geeft (reeksen, dag):
    - reeksen: meetlocatie_id, meetreeks_id, meetreeks_code, eenheid, filters,
      metingen (aantal unieke tijdstippen), van, tot (eerste en laatste dag);
    - dag: meetreeks_id, datum (UTC-dag), n, gem.

    Twee queries: de meetreeksen (klein) en de daggemiddelden, de enige die
    door `meting` gaat.
    """
    # Pas hier: zonder warehouse hoeft de connector niet geladen te worden.
    from databricks import sql
    from databricks.sdk.core import Config

    gekozen = f"""
    {_REEKS_LOCATIE},
    gekozen AS (
        SELECT DISTINCT * FROM reeks_locatie WHERE meetlocatie_id IN ({_ids_sql(meetlocatie_ids)})
    )"""

    cfg = Config()
    with sql.connect(
        server_hostname=cfg.host.removeprefix("https://").rstrip("/"),
        http_path=f"/sql/1.0/warehouses/{os.environ[WAREHOUSE_VAR].strip()}",
        credentials_provider=lambda: cfg.authenticate,
    ) as verbinding, verbinding.cursor() as cursor:
        # to_date volgt de sessietijdzone; de dagen zijn UTC-dagen, net als in de export.
        cursor.execute("SET TIME ZONE 'UTC'")
        reeksen = _query(cursor, f"WITH {gekozen} SELECT * FROM gekozen")
        # De enige query die `meting` leest. meting.id is niet uniek en een
        # tijdstip kan dubbel staan: eerst één waarde per reeks per tijdstip.
        dag = _query(cursor, f"""
            WITH {gekozen},
            per_tijdstip AS (
                SELECT m.meetreeks_id, m.tijdstempel_utc, avg(m.waarde) AS waarde
                FROM {paths.TBL_METING} m
                WHERE m.meetreeks_id IN (SELECT meetreeks_id FROM gekozen) AND m.waarde IS NOT NULL
                GROUP BY m.meetreeks_id, m.tijdstempel_utc
            )
            SELECT meetreeks_id, to_date(tijdstempel_utc) AS datum,
                   count(*) AS n, round(avg(waarde), 4) AS gem
            FROM per_tijdstip
            GROUP BY meetreeks_id, to_date(tijdstempel_utc)
        """)
    dag["datum"] = pd.to_datetime(dag["datum"])

    per_reeks = dag.groupby("meetreeks_id").agg(
        metingen=("n", "sum"), van=("datum", "min"), tot=("datum", "max")
    )
    reeksen = reeksen.merge(per_reeks, left_on="meetreeks_id", right_index=True, how="left")
    reeksen["metingen"] = reeksen["metingen"].fillna(0).astype(int)
    return reeksen, dag
