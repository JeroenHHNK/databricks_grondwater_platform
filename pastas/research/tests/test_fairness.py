"""The rules that make the comparison fair: no future forcing, no test heads, the same R2."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from research import features
from research.data import Series
from research.evaluate import FOLDS, global_training, make_task, r2
from research.models import Context, arx, trees

DAYS = pd.date_range("2020-01-01", "2024-12-31", freq="D")


@pytest.fixture(scope="module")
def ctx():
    rng = np.random.default_rng(0)
    precipitation = pd.Series(rng.gamma(0.5, 4.0, len(DAYS)) / 1000, DAYS)
    evaporation = pd.Series((1.5 + np.sin(2 * np.pi * DAYS.day_of_year / 365.25)) / 1000, DAYS)
    return Context.build(precipitation, evaporation)


@pytest.fixture(scope="module")
def series(ctx):
    index = pd.date_range("2022-06-01", "2024-10-31", freq="D")
    head = 0.002 * ctx.regression.loc[index, "N_90"] + 0.001 * ctx.regression.loc[index, "N_30"]
    head = head + np.random.default_rng(1).normal(0, 0.01, len(head))
    return Series("id-1", "series_1", head[np.random.default_rng(2).random(len(head)) > 0.1], "2022-06-01")


def test_features_never_look_ahead(ctx):
    cut = pd.Timestamp("2023-06-30")
    changed = ctx.forcing.copy()
    changed.loc[changed.index > cut] *= 5
    for build in (features.ml_features, features.regression_features):
        before, after = build(ctx.forcing), build(changed)
        pd.testing.assert_frame_equal(before.loc[:cut], after.loc[:cut])


def test_folds_split_like_holdout_r2(series):
    task = make_task(series, "holdout")
    split = series.head.index[int(len(series.head) * 0.7)]
    assert task.split == split
    assert task.train.index.max() == split and task.evaluate.index.min() > split
    assert len(task.train) + len(task.evaluate) == len(series.head)
    cv = make_task(series, "cv50")
    assert cv.evaluate.index.max() == series.head.index[int(len(series.head) * 0.7)]
    assert set(FOLDS) == {"full", "holdout", "cv50", "cv60"}


def test_r2_is_one_minus_sse_over_sst():
    observed = pd.Series([1.0, 2.0, 3.0, 4.0] * 5, pd.date_range("2024-01-01", periods=20))
    simulated = observed + 0.5
    expected = 1 - (0.25 * 20) / float(((observed - observed.mean()) ** 2).sum())
    assert r2(observed, simulated) == pytest.approx(expected)
    assert r2(observed.iloc[:5], simulated) is None  # too few days


@pytest.mark.parametrize("method", [arx.simulate_dlm_ols, arx.simulate_arx, trees.simulate_rf])
def test_simulation_ignores_heads_after_the_split(series, ctx, method):
    task = make_task(series, "holdout")
    tampered = Series(series.meetreeks_id, series.name,
                      series.head.where(series.head.index <= task.split, series.head + 10.0), series.tmin)
    a = method(task, ctx)
    b = method(make_task(tampered, "holdout"), ctx)
    pd.testing.assert_series_equal(a, b)
    assert a.index[-1] == series.head.index[-1]


def test_arx_recovers_a_stable_process(series, ctx):
    simulated = arx.simulate_arx(make_task(series, "holdout"), ctx)
    assert r2(make_task(series, "holdout").evaluate, simulated) > 0.8


def test_unstable_ar_is_rejected():
    assert arx.stable(np.array([0.5]))
    assert not arx.stable(np.array([1.05]))
    assert not arx.stable(np.array([1.5, -0.4]))  # a root at z ~ 0.87


def test_global_models_stop_at_the_earliest_split(series):
    later = Series("id-2", "series_2", series.head.loc["2023-03-01":], "2023-03-01")
    tasks = [make_task(series, "holdout"), make_task(later, "holdout")]
    cutoff = min(t.split for t in tasks)
    for head in global_training(tasks).values():
        assert head.index.max() <= cutoff
