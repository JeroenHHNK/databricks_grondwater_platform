import json
import os

import numpy as np
import pandas as pd
import pytest

from tarso_pipeline.fit import fit_series
from tarso_pipeline.fit_metrics import (
    FIT_METRICS_SCHEMA,
    empty_row,
    error_row,
    observed_fields,
    rows_to_frame,
    spark_schema,
)


def test_empty_row_has_every_column():
    assert set(empty_row()) == set(FIT_METRICS_SCHEMA)


def test_error_row_has_every_column_and_is_marked_failed():
    row = error_row("run", "abc", "name", "tarso", "ValueError: boom")
    assert set(row) == set(FIT_METRICS_SCHEMA)
    assert row["success"] is False
    assert row["error_message"] == "ValueError: boom"
    assert row["pastas_version"]


def test_rows_to_frame_pins_column_order():
    frame = rows_to_frame([empty_row(), empty_row()])
    assert list(frame.columns) == list(FIT_METRICS_SCHEMA)


def test_spark_schema_covers_the_handover_fields():
    ddl = spark_schema()
    for field in ("nfev int", "nobs int", "noise boolean", "warmup_days int",
                  "evp double", "r2 double", "rmse double", "aicc double",
                  "bic double", "obj double", "interp boolean",
                  "parameters string", "model_path string"):
        assert field in ddl


def test_observed_fields_of_an_empty_series():
    fields = observed_fields(pd.Series(dtype="float64"))
    assert fields["span_days"] == 0
    assert fields["first_entry"] is None


def test_observed_fields_reports_the_extent():
    index = pd.date_range("2021-01-01", periods=366, freq="D")
    fields = observed_fields(pd.Series(np.arange(366.0), index=index))
    assert fields["span_days"] == 365
    assert fields["min_value"] == 0.0
    assert fields["max_value"] == 365.0


# --- fit_series never raises ------------------------------------------------


def raw(start, days, per_day=1):
    index = pd.date_range(start, periods=days, freq="D").repeat(per_day)
    return pd.DataFrame({"tijdstempel": index, "waarde": np.linspace(-2, -1, len(index))})


def forcing():
    index = pd.date_range("2020-01-01", periods=2000, freq="D")
    rng = np.random.default_rng(0)
    p = pd.Series(rng.gamma(0.6, 3.0, 2000), index=index)
    e = pd.Series(np.abs(np.sin(np.arange(2000) / 58.0)) * 2, index=index)
    return p, e


def test_a_bad_frame_becomes_a_row_not_an_exception():
    p, e = forcing()
    row = fit_series("a" * 64, "s", pd.DataFrame({"x": [1]}), p, e, "run")
    assert row["success"] is False
    assert "Expected columns" in row["error_message"]
    assert set(row) == set(FIT_METRICS_SCHEMA)


def test_too_few_observations_is_reported_with_its_reason():
    p, e = forcing()
    row = fit_series("a" * 64, "s", raw("2021-01-01", 40), p, e, "run", min_obs=100)
    assert row["success"] is False
    assert "min_obs=100" in row["error_message"]
    # The cleaning facts established before the failure are kept.
    assert row["n_raw_rows"] == 40


def test_too_short_is_reported_with_its_reason():
    p, e = forcing()
    row = fit_series(
        "a" * 64, "s", raw("2021-01-01", 200), p, e, "run", min_obs=10, min_days=365
    )
    assert row["success"] is False
    assert "min_days=365" in row["error_message"]


def test_unknown_model_type_is_a_row_not_a_crash():
    p, e = forcing()
    row = fit_series(
        "a" * 64, "s", raw("2021-01-01", 800), p, e, "run", model_type="nope"
    )
    assert row["success"] is False
    assert "nope" in row["error_message"]


def test_identity_fields_survive_a_failure():
    p, e = forcing()
    row = fit_series("abc" * 10, "my_series", pd.DataFrame({"x": [1]}), p, e, "run_42")
    assert row["run_id"] == "run_42"
    assert row["meetreeks_id"] == "abc" * 10
    assert row["series_name"] == "my_series"


# --- the real fit -----------------------------------------------------------


@pytest.mark.slow
@pytest.mark.parametrize("model_type", ["tarso", "recharge"])
def test_a_real_fit_populates_the_metrics(tmp_path, model_type):
    import matplotlib

    matplotlib.use("Agg")
    p, e = forcing()
    # Duplicated timestamps on purpose: the fit must survive the dedup path.
    row = fit_series(
        "a" * 64, "well", raw("2020-01-01", 1200, per_day=3), p, e, "run",
        model_type=model_type, run_directory=str(tmp_path),
    )

    assert row["success"] is True, row["error_message"]
    assert row["n_duplicate_timestamps"] == 2400
    assert row["nobs"] == 1200
    for field in ("evp", "r2", "rmse", "aicc", "bic", "obj", "nfev"):
        assert row[field] is not None, field
    assert row["freq"] == "D"
    assert json.loads(row["parameters"])  # parameters are JSON, not a repr
    # Artefacts land in the three folders of the run directory, by kind.
    assert row["model_path"].endswith(f"pas_files{os.sep}well_{model_type}.pas")
    assert row["plot_path"].endswith(
        f"results_files{os.sep}well_{model_type}_results.png"
    )
    assert row["diagnostics_path"].endswith(
        f"diagnostics_files{os.sep}well_{model_type}_diagnostics.png"
    )
    for key in ("model_path", "plot_path", "diagnostics_path"):
        assert os.path.isfile(row[key]), key

    import pastas as ps

    assert ps.io.load(row["model_path"]) is not None
