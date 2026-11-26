"""Extra stresses are held at their calibration level on IMPREX (``hold_other_stresses``)."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

ps = pytest.importorskip("pastas")

from tarso_pipeline.simulate import hold_other_stresses, simulate_member  # noqa: E402


def _model():
    index = pd.date_range("2010-01-01", "2016-12-31", freq="D")
    rng = np.random.default_rng(0)
    precipitation = pd.Series(rng.gamma(0.5, 4.0, len(index)) / 1000, index)
    evaporation = pd.Series((1.5 + np.sin(2 * np.pi * index.day_of_year / 365.25)) / 1000, index)
    level = pd.Series(0.2 * np.sin(2 * np.pi * index.day_of_year / 365.25) + rng.normal(0, 0.05, len(index)), index)
    model = ps.Model(pd.Series(0.0, index[2000::5]), name="test")
    model.add_stressmodel(ps.RechargeModel(precipitation, evaporation, rfunc=ps.Exponential(), name="Recharge"))
    model.add_stressmodel(ps.StressModel(level - level.mean(), rfunc=ps.Exponential(), name="level",
                                         settings="waterlevel"))
    model.parameters.loc[["Recharge_A", "Recharge_a", "level_A", "level_a"], "initial"] = [500.0, 50.0, 0.5, 5.0]
    model.settings["tmin"], model.settings["tmax"] = index[2000], index[-1]
    return model, precipitation, evaporation


@pytest.mark.slow
@pytest.mark.parametrize("hold", ["mean", "climatology"])
def test_other_stresses_are_held(hold):
    model, precipitation, evaporation = _model()
    member = pd.date_range("1951-01-01", "1960-12-31", freq="D")
    held = hold_other_stresses(model, "Recharge", member, hold=hold)
    assert held == ["level"]
    series = model.stressmodels["level"].stress[0].series_original
    assert series.index.equals(member)
    if hold == "mean":
        assert series.nunique() == 1
    else:
        # the same value on the same day of every year
        assert series.groupby(series.index.day_of_year).nunique().max() == 1


@pytest.mark.slow
def test_simulate_member_holds_the_level_at_its_mean():
    model, precipitation, evaporation = _model()
    member = pd.date_range("1951-01-01", "1960-12-31", freq="D")
    p = pd.Series(np.resize(precipitation.to_numpy(), len(member)), member)
    e = pd.Series(np.resize(evaporation.to_numpy(), len(member)), member)
    simulate_member(model, p, e, warmup_years=2)
    contribution = model.get_contribution("level", tmin="1954-01-01", tmax="1960-12-31", warmup=3650)
    assert contribution.std() < 1e-9


def test_unknown_hold_mode_is_refused():
    model, *_ = _model()
    with pytest.raises(ValueError):
        hold_other_stresses(model, "Recharge", pd.date_range("1951", periods=3), hold="random")


@pytest.mark.slow
def test_simulate_member_uses_a_synthetic_stress_instead_of_holding_it():
    model, precipitation, evaporation = _model()
    member = pd.date_range("1951-01-01", "1960-12-31", freq="D")
    p = pd.Series(np.resize(precipitation.to_numpy(), len(member)), member)
    e = pd.Series(np.resize(evaporation.to_numpy(), len(member)), member)
    level = pd.Series(np.sin(np.arange(len(member)) / 30.0) * 0.1, member)
    simulate_member(model, p, e, warmup_years=2, synthetic={"level": level})
    series = model.stressmodels["level"].stress[0].series_original
    assert np.allclose(series.to_numpy(), level.to_numpy())
    contribution = model.get_contribution("level", tmin="1954-01-01", tmax="1960-12-31", warmup=3650)
    assert contribution.std() > 1e-4


def test_synthetic_stress_for_an_unknown_stress_model_is_refused():
    model, precipitation, evaporation = _model()
    member = pd.date_range("1951-01-01", "1956-12-31", freq="D")
    p = pd.Series(np.resize(precipitation.to_numpy(), len(member)), member)
    e = pd.Series(np.resize(evaporation.to_numpy(), len(member)), member)
    with pytest.raises(ValueError):
        simulate_member(model, p, e, warmup_years=2, synthetic={"boezem": p})
