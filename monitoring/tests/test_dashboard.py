"""The dashboard definition: every widget points at a dataset column that is really produced."""

import datetime as dt
import json
import re

from monitoring_lib import flags, sql
from monitoring_lib.dashboard import build, definition
from monitoring_lib.lvdash import problems

DAY = dt.date(2026, 1, 1)
# Columns of tables the monitoring job does not build (validated_meting, valid_pastas_simulations).
EXTERNAL = {"tijdstempel_utc", "waarde", "meetreeks_id", "run_id", "model_type", "datum", "simulated"}


def _produced() -> set[str]:
    texts = [sql.hourly_sql(), sql.series_status_sql(DAY), sql.uptime_daily_sql("c.s.t", DAY, 10),
             sql.flag_input_sql("c.s.t"), sql.change_recent_sql("c.s.t", DAY, "c.s.k"),
             sql.pastas_models_sql("valid_pastas", {"fit_metrics": True, "model_checks": True,
                                                    "model_selection": True}, "c", "s"),
             sql.pastas_series_sql("c.s.m", "c.s.t")]
    aliases = set(re.findall(r"\bAS\s+([a-z_][a-z0-9_]*)", " ".join(texts), flags=re.IGNORECASE))
    plain = set(re.findall(r"\b[a-z]{1,3}\.([a-z_][a-z0-9_]*)\b", " ".join(texts)))
    return aliases | plain | set(flags.FLAG_SCHEMA) | EXTERNAL


def test_no_problems():
    datasets, pages = definition()
    assert problems(datasets, pages) == []


def test_columns_read_from_monitoring_tables_are_produced():
    produced = _produced()
    datasets, _ = definition()
    missing = {}
    for d in datasets:
        qualified = set(re.findall(r"\b[a-z]{1,3}\.([a-z_][a-z0-9_]*)\b", d.sql))
        own = set(re.findall(r"\bAS\s+([a-z_][a-z0-9_]*)", d.sql, flags=re.IGNORECASE))  # its CTEs
        unknown = sorted(c for c in qualified if c not in produced | own)
        if unknown:
            missing[d.name] = unknown
    assert missing == {}


def test_json_is_complete():
    board = json.loads(json.dumps(build()))
    assert [p["displayName"] for p in board["pages"]] == [
        "Data-inventarisatie & datastroom", "Meetinventarisatie & datakwaliteit", "Reeks-specifiek", "Pastas"]
    names = {d["name"] for d in board["datasets"]}
    for page in board["pages"]:
        for item in page["layout"]:
            for q in item["widget"].get("queries", []):
                assert q["query"]["datasetName"] in names
            assert item["position"]["x"] + item["position"]["width"] <= 12
