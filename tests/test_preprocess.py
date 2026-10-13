import numpy as np
import pandas as pd
import pytest

from tarso_pipeline.io import HeadFormat, LoadedHead
from tarso_pipeline.preprocess import prepare_head


def loaded(index, values, fmt=HeadFormat.RAW):
    s = pd.Series(values, index=pd.DatetimeIndex(index), dtype=float, name="head")
    return LoadedHead(series=s, format=fmt, n_rows_read=len(s), n_rejected=0)


def test_daily_mean_of_hourly_data():
    idx = pd.date_range("2024-01-01", periods=48, freq="h")
    out = prepare_head(loaded(idx, np.arange(48))).series
    assert len(out) == 2
    assert out.iloc[0] == pytest.approx(np.arange(24).mean())


def test_gaps_are_not_filled():
    idx = list(pd.date_range("2024-01-01", periods=24, freq="h")) + list(
        pd.date_range("2024-01-05", periods=24, freq="h")
    )
    out = prepare_head(loaded(idx, np.ones(48))).series
    assert len(out) == 2  # days 2-4 stay absent, no NaN rows
    assert not out.isna().any()


def test_skip_validated_below_min_obs():
    idx = pd.date_range("2024-01-01", periods=10, freq="D")
    res = prepare_head(loaded(idx, np.ones(10), HeadFormat.VALIDATED), min_obs=100)
    assert res.skipped
    assert "min_obs=100" in res.skip_reason


def test_raw_never_skipped_for_min_obs():
    idx = pd.date_range("2024-01-01", periods=10, freq="D")
    res = prepare_head(loaded(idx, np.ones(10), HeadFormat.RAW), min_obs=100)
    assert not res.skipped


def test_validated_with_enough_obs_not_skipped():
    idx = pd.date_range("2024-01-01", periods=150, freq="D")
    res = prepare_head(loaded(idx, np.ones(150), HeadFormat.VALIDATED), min_obs=100)
    assert not res.skipped


def test_other_rule_and_method():
    idx = pd.date_range("2024-01-01", periods=48, freq="h")
    out = prepare_head(loaded(idx, np.arange(48)), rule="D", method="max").series
    assert out.tolist() == [23.0, 47.0]


def test_invalid_method_rejected():
    idx = pd.date_range("2024-01-01", periods=5, freq="D")
    with pytest.raises(ValueError):
        prepare_head(loaded(idx, np.ones(5)), method="__class__")


def test_empty_validated_series_is_skipped_not_crashing():
    res = prepare_head(loaded([], [], HeadFormat.VALIDATED))
    assert res.skipped
    assert len(res.series) == 0
