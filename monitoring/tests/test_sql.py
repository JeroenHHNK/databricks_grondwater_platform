"""The SQL builders: names, the check labels the pipeline writes, older fit_metrics tables."""

import datetime as dt
from pathlib import Path

import pandas as pd
import pytest

from monitoring_lib import sql

AS_OF = dt.date(2026, 11, 17)
EXPORT = Path(__file__).resolve().parents[2] / "pastas" / "results_export"


def test_table_names():
    assert sql.table("series_status", "c", "s") == "c.s.monitoring_series_status"
    with pytest.raises(ValueError):
        sql.table("nope", "c", "s")


def test_names_and_ids_are_checked():
    with pytest.raises(ValueError):
        sql.validated_hours_sql("x; DROP TABLE y")
    with pytest.raises(ValueError):
        sql.flag_input_sql("c.s.t", ["' OR 1=1 --"])
    assert f"'{'ab' * 32}'" in sql.flag_input_sql("c.s.t", ["ab" * 32])


def test_status_without_optional_tables():
    text = sql.series_status_sql(AS_OF)
    assert "WHERE 1 = 0" in text and "DATE'2026-11-17'" in text
    assert "valid_pastas_fit_metrics" in sql.series_status_sql(AS_OF, "c.s.valid_pastas_fit_metrics")


def test_check_patterns_match_the_labels_the_pipeline_writes():
    checks = EXPORT / "valid_pastas" / "model_checks.csv"
    if not checks.is_file():
        pytest.skip("no exported model_checks")
    labels = pd.read_csv(checks)["check"].astype(str)
    pivot = sql.checks_pivot_sql("c.s.valid_pastas_model_checks")
    for pattern in sql._CHECK_KINDS.values():
        assert labels.str.startswith(pattern.rstrip("%")).any(), pattern
        assert f"LIKE '{pattern}'" in pivot


def test_pastas_models_with_an_older_fit_metrics_table():
    text = sql.pastas_models_sql("unvalid_pastas", {"fit_metrics": True}, "c", "s",
                                 fit_columns={"run_id", "meetreeks_id", "model_type", "success", "r2", "evp",
                                              "rmse", "aic", "t95_level0_days", "t95_level1_days"})
    assert "f.t95_days" not in text and "f.r2_holdout" not in text and "f.t95_level0_days" in text
    assert "_model_selection" not in text and "'tarso'" in text
    with pytest.raises(ValueError):
        sql.pastas_models_sql("valid_pastas", {}, "c", "s")


def test_map_colours_cover_every_case():
    text = sql.pastas_series_sql("c.s.monitoring_pastas_models", "c.s.monitoring_series_status")
    for colour in (sql.KLEUR_GROEN, sql.KLEUR_GEEL, sql.KLEUR_ROOD, sql.KLEUR_GRIJS):
        assert f"'{colour}'" in text
