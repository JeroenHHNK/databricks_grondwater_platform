"""Local forcing, extra stresses and variant builders."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from research.accepted import report, stresses, variants


def test_idw_weights_sum_to_one_and_favour_the_nearest():
    weights = stresses.idw_weights((52.51, 5.04), stresses.RD_STATIONS, n=3)
    assert len(weights) == 3 and sum(weights.values()) == pytest.approx(1.0)
    assert max(weights, key=weights.get) == 224  # Edam


def test_weighted_mean_renormalises_over_missing_stations():
    table = pd.DataFrame({1: [1.0, np.nan], 2: [3.0, 3.0]}, index=pd.date_range("2024-01-01", periods=2))
    out = stresses.weighted(table, {1: 0.5, 2: 0.5})
    assert out.tolist() == [2.0, 3.0]


def test_rd_shift_moves_the_label_one_day_back(monkeypatch):
    table = pd.DataFrame({224: [0.0, 5.0, 0.0]}, index=pd.date_range("2024-01-01", periods=3))
    monkeypatch.setattr(stresses, "_pivot", lambda path, column: table)
    monkeypatch.setattr(stresses, "idw_weights", lambda *a, **k: {224: 1.0})
    shifted = stresses.local_precipitation((52.5, 5.0))
    assert shifted.idxmax() == pd.Timestamp("2024-01-01")  # the 08-08 sum ending on 2 Jan
    assert stresses.local_precipitation((52.5, 5.0), shift_days=0).idxmax() == pd.Timestamp("2024-01-02")


def test_anomaly_has_zero_mean_over_the_calibration_days():
    level = pd.Series(np.arange(10.0), pd.date_range("2024-01-01", periods=10))
    head = level.iloc[2:6]
    assert stresses.anomaly(level, head).loc[head.index].mean() == pytest.approx(0.0)


def test_variant_names_and_tarso_without_extra_stress():
    assert variants.parse("linear_gamma@localP+wl") == ("linear_gamma", ["localP", "wl"])
    assert not variants.applicable("tarso@wl")
    assert variants.applicable("tarso@localP")
    assert "tarso@wl" not in variants.names(["wl"])
    assert report.group_of("flex_gamma@localP") == "flex@localP"
    assert report.group_of("linear_exp") == "baseline"


def test_monthly_factors_scale_the_mean():
    index = pd.date_range("2000-01-01", "2003-12-31", freq="D")
    reference = pd.Series(2.0, index)
    factors = report.monthly_factors(reference * 1.1, reference)
    assert np.allclose(factors.to_numpy(), 1.1)


@pytest.mark.slow
def test_flex_keeps_the_fixed_parameters_fixed():
    ps = pytest.importorskip("pastas")
    index = pd.date_range("2015-01-01", "2020-12-31", freq="D")
    rng = np.random.default_rng(0)
    p = pd.Series(rng.gamma(0.5, 4.0, len(index)) / 1000, index)
    e = pd.Series(1.7 / 1000, index)
    head = pd.Series(rng.normal(0, 0.1, len(index)), index).iloc[1500:]
    model = variants.build("flex_gamma", "s", head, variants.Inputs(p, e))
    fixed = model.parameters.loc[["Recharge_kv", "Recharge_lp", "Recharge_simax"], "vary"]
    assert not fixed.any()
    assert isinstance(model.stressmodels["Recharge"].recharge, ps.rch.FlexModel)
