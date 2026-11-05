import numpy as np
import pandas as pd
import pytest

from tarso_pipeline.fit import fit_series
from tarso_pipeline.model_checks import (
    MODEL_CHECKS_SCHEMA,
    frame_to_rows,
    rows_to_frame,
    spark_schema,
)

IDENTITY = {"run_id": "run", "meetreeks_id": "a" * 64,
            "series_name": "well", "model_type": "tarso"}


def checklist_frame():
    """The shape ``ps.check.checklist`` returns, one scalar and one bounds check."""
    frame = pd.DataFrame(
        {
            "statistic": [0.9, 37.4],
            "operator": [">=", "within"],
            "threshold": [0.7, (0.0, 200.0)],
            "dimensions": ["[-]", "[T]"],
            "pass": [True, np.bool_(False)],
            "comment": ["", "near the bound"],
        },
        index=pd.Index(["rsq>=0.7", "Bounds: Tarso_a0"], name="check"),
    )
    return frame


def test_spark_schema_quotes_the_sql_keywords():
    ddl = spark_schema()
    assert "`check` string" in ddl
    assert "`comment` string" in ddl
    assert "`passed` boolean" in ddl


def test_frame_to_rows_keeps_order_identity_and_every_column():
    rows = frame_to_rows(checklist_frame(), IDENTITY)
    assert [r["check_order"] for r in rows] == [0, 1]
    for row in rows:
        assert set(row) == set(MODEL_CHECKS_SCHEMA)
        assert row["series_name"] == "well"
        assert row["checklist"] == "brakenhoff_2022"
    assert rows[0]["passed"] is True
    assert rows[1]["passed"] is False  # numpy bool becomes a Python bool
    assert rows[0]["comment"] is None  # empty comment is NULL, not ""


def test_a_bounds_threshold_splits_into_min_and_max():
    scalar, bounds = frame_to_rows(checklist_frame(), IDENTITY)
    assert scalar["threshold"] == 0.7
    assert scalar["threshold_min"] is None
    assert bounds["threshold"] is None
    assert (bounds["threshold_min"], bounds["threshold_max"]) == (0.0, 200.0)


def test_rows_to_frame_pins_column_order():
    frame = rows_to_frame(frame_to_rows(checklist_frame(), IDENTITY))
    assert list(frame.columns) == list(MODEL_CHECKS_SCHEMA)


def test_a_failed_fit_adds_no_check_rows():
    index = pd.date_range("2020-01-01", periods=2000, freq="D")
    p = e = pd.Series(1.0, index=index)
    checks = []
    row = fit_series("a" * 64, "s", pd.DataFrame({"x": [1]}), p, e, "run",
                     check_rows=checks)
    assert row["success"] is False
    assert checks == []


@pytest.mark.slow
@pytest.mark.parametrize("model_type", ["tarso", "recharge"])
def test_a_real_fit_is_checked(model_type):
    index = pd.date_range("2015-01-01", periods=3000, freq="D")
    rng = np.random.default_rng(0)
    p = pd.Series(rng.gamma(0.6, 3.0, len(index)), index=index) / 1000
    e = pd.Series(np.abs(np.sin(np.arange(len(index)) / 58.0)) * 2, index=index) / 1000
    head = (p - e).rolling(60).sum().fillna(0) * 5 - 1.5
    head = head.loc["2020":] + rng.normal(0, 0.03, len(head.loc["2020":]))
    raw = pd.DataFrame({"tijdstempel": head.index, "waarde": head.values})

    checks = []
    row = fit_series("a" * 64, "well", raw, p, e, "run", model_type=model_type,
                     check_rows=checks)

    assert row["success"] is True, row["error_message"]
    names = [c["check"] for c in checks]
    assert "checklist_error" not in names, checks
    assert any(n.startswith("rsq>=") for n in names)
    assert any(n.startswith("runs_test") for n in names)
    assert any(n.startswith("Bounds:") for n in names)
    gains = [n for n in names if n.startswith("|")]
    if model_type == "tarso":
        # Both gains, and the memory of both drainage levels.
        assert gains == ["|Tarso_A0| > 1.96σ", "|Tarso_A1| > 1.96σ"]
        assert sum(n.startswith("t95_Tarso") for n in names) == 2
    else:
        assert gains == ["|Recharge_A| > 1.96σ"]
    assert all(c["passed"] in (True, False) for c in checks)
