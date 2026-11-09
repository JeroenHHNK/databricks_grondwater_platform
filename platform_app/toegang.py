"""
Can this app read live data, and could it write validation results to a Delta table?

Each check runs one small query as a given identity and says what is missing. Kept
free of Streamlit: `controleer(cursor, …)` takes any DB-API cursor, so it can be tested
with a fake one and run from a notebook.

The identity is whoever the connection logs in as: in a Databricks App the app's
service principal (or, with user authorization, the visitor); locally your own
~/.databrickscfg profile. `current_user()` names it, and the GRANT statements below
are filled in with that name.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from modules import paths

# Where validation results would go. The data validation notebooks write to the personal
# schema today (see data_validation/docs/DATABRICKS_PERMISSIONS.md §3); a shared project
# schema is a better home once the app writes there too.
SCHEMA_VAR = "VALIDATION_SCHEMA"
STANDAARD_SCHEMA = "waterkeringbeheer_prd.jvanruitenbeek"
VALIDATIE_TABEL = "validated_meting"
SCHRIJFTEST_TABEL = "__app_schrijftest"

BRON_TABELLEN: list[str] = [paths.TBL_METING, paths.TBL_MEETREEKS, paths.TBL_MEETLOCATIE]


@dataclass(frozen=True)
class Uitkomst:
    controle: str
    gelukt: bool
    detail: str
    oplossing: str = ""  # SQL or a step that fixes a failure; empty when it passed


def doel_schema() -> str:
    return os.environ.get(SCHEMA_VAR, "").strip() or STANDAARD_SCHEMA


def _fout(fout: Exception) -> str:
    tekst = str(fout).strip().splitlines()[0] if str(fout).strip() else type(fout).__name__
    return tekst[:300]


def _schemas(tabel: str) -> tuple[str, str]:
    catalog, schema, _ = tabel.split(".")
    return catalog, f"{catalog}.{schema}"


def _lees_grants(tabel: str, wie: str) -> str:
    catalog, schema = _schemas(tabel)
    return (f"GRANT USE CATALOG ON CATALOG {catalog} TO `{wie}`;\n"
            f"GRANT USE SCHEMA ON SCHEMA {schema} TO `{wie}`;\n"
            f"GRANT SELECT ON TABLE {tabel} TO `{wie}`;")


def _schrijf_grants(schema: str, wie: str) -> str:
    catalog = schema.split(".")[0]
    return (f"GRANT USE CATALOG ON CATALOG {catalog} TO `{wie}`;\n"
            f"GRANT USE SCHEMA, CREATE TABLE ON SCHEMA {schema} TO `{wie}`;\n"
            f"-- and on the table, once it exists:\n"
            f"GRANT SELECT, MODIFY ON TABLE {schema}.{VALIDATIE_TABEL} TO `{wie}`;")


def wie_ben_ik(cursor) -> str:
    cursor.execute("SELECT current_user()")
    return str(cursor.fetchone()[0])


def controleer(cursor, schema: str | None = None, schrijftest: bool = False) -> tuple[str, list[Uitkomst]]:
    """
    Runs the checks on `cursor` and returns (identity, outcomes). Read-only unless
    `schrijftest`: then it creates, fills and drops `<schema>.__app_schrijftest`, the
    same create → insert → read → drop the notebooks were checked with.
    """
    schema = schema or doel_schema()
    wie = wie_ben_ik(cursor)
    uit: list[Uitkomst] = [Uitkomst("Verbinding met het SQL warehouse", True, f"ingelogd als `{wie}`")]

    for tabel in BRON_TABELLEN:
        try:
            cursor.execute(f"SELECT 1 FROM {tabel} LIMIT 1")
            cursor.fetchall()
            uit.append(Uitkomst(f"Lezen: {tabel}", True, "SELECT werkt"))
        except Exception as fout:  # noqa: BLE001 - every failure is an outcome
            uit.append(Uitkomst(f"Lezen: {tabel}", False, _fout(fout), _lees_grants(tabel, wie)))

    try:
        cursor.execute(f"SHOW TABLES IN {schema}")
        tabellen = {str(r[1]) for r in cursor.fetchall()}
        uit.append(Uitkomst(f"Schema {schema} zien", True, f"{len(tabellen)} tabellen"))
    except Exception as fout:  # noqa: BLE001
        tabellen = None
        catalog = schema.split(".")[0]
        uit.append(Uitkomst(f"Schema {schema} zien", False, _fout(fout),
                            f"GRANT USE CATALOG ON CATALOG {catalog} TO `{wie}`;\n"
                            f"GRANT USE SCHEMA ON SCHEMA {schema} TO `{wie}`;"))

    if tabellen is not None:
        if VALIDATIE_TABEL in tabellen:
            try:
                cursor.execute(f"SELECT count(*) FROM {schema}.{VALIDATIE_TABEL}")
                n = cursor.fetchone()[0]
                uit.append(Uitkomst(f"Lezen: {schema}.{VALIDATIE_TABEL}", True, f"{n} rijen"))
            except Exception as fout:  # noqa: BLE001
                uit.append(Uitkomst(f"Lezen: {schema}.{VALIDATIE_TABEL}", False, _fout(fout),
                                    f"GRANT SELECT ON TABLE {schema}.{VALIDATIE_TABEL} TO `{wie}`;"))
        else:
            uit.append(Uitkomst(f"Lezen: {schema}.{VALIDATIE_TABEL}", True,
                                "bestaat nog niet (03_validated_to_delta maakt hem)"))

    if schrijftest:
        tabel = f"{schema}.{SCHRIJFTEST_TABEL}"
        stap = "CREATE TABLE"
        try:
            cursor.execute(f"CREATE TABLE IF NOT EXISTS {tabel} (id INT, wie STRING)")
            stap = "INSERT"
            cursor.execute(f"INSERT INTO {tabel} VALUES (1, current_user())")
            stap = "SELECT"
            cursor.execute(f"SELECT count(*) FROM {tabel}")
            cursor.fetchone()
            stap = "DROP TABLE"
            cursor.execute(f"DROP TABLE {tabel}")
            uit.append(Uitkomst(f"Schrijven in {schema}", True, "create → insert → read → drop gelukt"))
        except Exception as fout:  # noqa: BLE001
            uit.append(Uitkomst(f"Schrijven in {schema}", False, f"{stap}: {_fout(fout)}",
                                _schrijf_grants(schema, wie)))
            try:
                cursor.execute(f"DROP TABLE IF EXISTS {tabel}")
            except Exception:  # noqa: BLE001 - best effort; the failure is already reported
                pass
    return wie, uit
