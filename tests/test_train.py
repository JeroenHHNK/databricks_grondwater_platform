"""Training tests. The Pastas fit itself is slow, so the heavy path is marked
``slow``; everything that guards the contract runs without fitting anything."""

import numpy as np
import pandas as pd
import pytest

from tarso_pipeline.train import (
    OUTPUT_SCHEMA,
    STAT_LABELS,
    TrainingContext,
    make_run_id,
    rows_to_frame,
    spark_schema,
    train_one,
)

FORCING_DAYS = 2000


@pytest.fixture
def forcing():
    index = pd.date_range("2020-01-01", periods=FORCING_DAYS, freq="D")
    rng = np.random.default_rng(0)
    precipitation = pd.Series(rng.gamma(0.6, 3.0, FORCING_DAYS), index=index, name="p")
    seasonal = 1.6 + 1.4 * np.sin(np.arange(FORCING_DAYS) * 2 * np.pi / 365.25)
    evaporation = pd.Series(seasonal.clip(min=0), index=index, name="e")
    return precipitation, evaporation


@pytest.fixture
def context(forcing, tmp_path):
    precipitation, evaporation = forcing
    return TrainingContext(
        run_id="26_10_13_00_00",
        precipitation=precipitation,
        evaporation=evaporation,
        prec_file="f.csv",
        prec_column="precipitation_mm",
        evap_file="f.csv",
        evap_column="makkink_mm",
        model_dir=str(tmp_path / "models"),
        min_days=365,
    )


def write_head(path, start, days, freq="D"):
    index = pd.date_range(start, periods=days, freq=freq)
    rng = np.random.default_rng(1)
    values = -2.0 + np.cumsum(rng.normal(0, 0.01, len(index)))
    pd.Series(values, index=index, name="head").to_csv(path, index_label="Time")
    return path


# --- the output contract ---------------------------------------------------


def test_row_keys_match_the_declared_schema(tmp_path, context):
    # The guard against drift: mapInPandas matches the schema positionally, so a
    # column added to the row and not to OUTPUT_SCHEMA would corrupt the table.
    path = write_head(tmp_path / "a.csv", "2021-01-01", 30)
    row = train_one(str(path), "survey", "a", context)
    assert set(row) == set(OUTPUT_SCHEMA)


def test_every_status_produces_the_same_columns(tmp_path, context):
    short = train_one(str(write_head(tmp_path / "s.csv", "2021-01-01", 30)), "s", "s", context)
    broken = tmp_path / "b.csv"
    broken.write_text("nonsense\n")
    failed = train_one(str(broken), "s", "b", context)
    assert set(short) == set(failed) == set(OUTPUT_SCHEMA)


def test_rows_to_frame_pins_column_order():
    frame = rows_to_frame([{name: None for name in OUTPUT_SCHEMA}])
    assert list(frame.columns) == list(OUTPUT_SCHEMA)


def test_spark_schema_is_ddl():
    ddl = spark_schema()
    assert ddl.startswith("run_id string, series string")
    assert "min_days int" in ddl
    assert "validation_filter boolean" in ddl


def test_all_spec_metrics_are_in_the_schema():
    for label in STAT_LABELS:
        assert OUTPUT_SCHEMA[label] == "double"
    assert OUTPUT_SCHEMA["response_tmax_95"] == "double"


# --- skipping --------------------------------------------------------------


def test_series_shorter_than_min_days_is_skipped_not_dropped(tmp_path, context):
    path = write_head(tmp_path / "short.csv", "2023-01-01", 120)
    row = train_one(str(path), "survey", "short", context)
    assert row["status"] == "skipped"
    assert "min_days=365" in row["error"]
    assert row["span_days"] == 119
    assert row["rsq"] is None


def test_skipped_row_still_reports_the_observed_summary(tmp_path, context):
    row = train_one(
        str(write_head(tmp_path / "s.csv", "2023-01-01", 10)), "survey", "s", context
    )
    assert row["first_entry"] == "2023-01-01"
    assert row["length_series"] == 10
    assert row["min_value"] is not None


def test_min_days_zero_trains_everything(tmp_path, forcing):
    precipitation, evaporation = forcing
    ctx = TrainingContext(
        run_id="r", precipitation=precipitation, evaporation=evaporation,
        prec_file="f", prec_column="p", evap_file="f", evap_column="e",
        min_days=0,
    )
    row = train_one(
        str(write_head(tmp_path / "s.csv", "2021-01-01", 40)), "survey", "s", ctx
    )
    # Not skipped for being short; it may still fail to fit, which is a
    # different status and the point of the distinction.
    assert row["status"] != "skipped" or "min_days" not in row["error"]


def test_empty_series_is_reported_distinctly_from_a_short_one(tmp_path, context):
    path = tmp_path / "empty.csv"
    path.write_text("Time,head\n2021-01-01 00:00:00,\n")
    row = train_one(str(path), "survey", "empty", context)
    assert row["status"] == "skipped"
    assert "no usable observations" in row["error"]


# --- failures are data, not crashes ----------------------------------------


def test_unreadable_file_becomes_an_error_row(tmp_path, context):
    row = train_one(str(tmp_path / "does_not_exist.csv"), "survey", "missing", context)
    assert row["status"] == "error"
    assert row["error"]
    assert row["series"] == "missing"
    assert row["run_id"] == context.run_id


def test_garbage_file_does_not_raise(tmp_path, context):
    path = tmp_path / "garbage.csv"
    path.write_text("\x00\x01 not a csv at all\n")
    row = train_one(str(path), "survey", "garbage", context)
    assert row["status"] in {"error", "skipped"}  # never an exception


def test_error_row_carries_the_exception_type(tmp_path, context):
    row = train_one(str(tmp_path / "nope.csv"), "survey", "nope", context)
    assert ":" in row["error"]  # "FileNotFoundError: ..."


# --- run identification ----------------------------------------------------


def test_run_id_format():
    import datetime as dt

    assert make_run_id(dt.datetime(2026, 10, 13, 14, 4)) == "26_10_13_14_04"


def test_parameters_are_recorded_on_every_row(tmp_path, context):
    row = train_one(
        str(write_head(tmp_path / "s.csv", "2023-01-01", 30)), "survey_x", "s", context
    )
    assert row["survey"] == "survey_x"
    assert row["min_days"] == 365
    assert row["min_obs"] == 100
    assert row["resample_rule"] == "D"
    assert row["validation_filter"] is True
    assert row["pastas_version"]
    assert row["model"] == "tarso"


# --- the real fit ----------------------------------------------------------


@pytest.mark.slow
def test_a_long_series_actually_fits_and_writes_a_pas_file(tmp_path, context):
    import matplotlib

    matplotlib.use("Agg")
    path = write_head(tmp_path / "long.csv", "2020-01-01", 1200)
    row = train_one(str(path), "survey", "long_well", context)

    assert row["status"] == "successful", row["error"]
    assert row["rsq"] is not None and row["rmse"] is not None
    assert row["tmin_cal"] == "2020-01-01"
    assert row["model_path"].endswith("long_well_tarso_model.pas")

    import pastas as ps

    assert ps.io.load(row["model_path"]) is not None
