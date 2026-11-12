import numpy as np
import pandas as pd
import pytest
from scipy.stats import genextreme

from tarso_pipeline.evt import (
    HESSIAN_MAX_SHAPE,
    bootstrap_gev_params,
    bootstrap_shape_se,
    hessian_shape_se,
    return_level,
    select_family,
    trim_tail,
)
from tarso_pipeline.uncertainty import (
    RETURN_LEVEL_CI_COLUMNS,
    SHAPE_UNCERTAINTY_COLUMNS,
    gev_band,
    gev_levels,
    model_uncertainty,
    pooled_series,
    read_pooled,
    tail_uncertainty,
)

POOLED = genextreme.rvs(0.05, -2.0, 0.15, size=704, random_state=1)


# --- the Hessian SE ----------------------------------------------------------


@pytest.mark.parametrize("shape", [-0.3, 0.0, 0.3])
@pytest.mark.parametrize("exclude_first", [0, 450])
def test_the_hessian_se_agrees_with_the_bootstrap(shape, exclude_first):
    sample = genextreme.rvs(shape, -2.0, 0.15, size=704, random_state=1)
    kept, _ = trim_tail(sample, exclude_first)
    params = genextreme.fit(kept)
    hessian = hessian_shape_se(kept, params)
    bootstrap = bootstrap_shape_se(kept, replicates=150, start=params)
    assert np.isfinite(hessian)
    assert 0.7 < hessian / bootstrap < 1.5


def test_the_hessian_se_shrinks_with_the_sample():
    small = genextreme.rvs(0.0, 0, 1, size=200, random_state=2)
    large = genextreme.rvs(0.0, 0, 1, size=2000, random_state=2)
    assert hessian_shape_se(large) < hessian_shape_se(small)


def test_the_hessian_se_refuses_the_non_regular_region():
    params = (HESSIAN_MAX_SHAPE + 0.1, 0.0, 1.0)
    assert np.isnan(hessian_shape_se(np.linspace(-1, 1, 300), params))


def test_selection_defaults_to_the_hessian_and_records_it():
    selection = select_family(POOLED)
    assert selection.shape_se_method == "hessian"
    assert selection.as_row()["shape_se_method"] == "hessian"


def test_selection_falls_back_to_the_bootstrap_when_the_hessian_is_unusable(monkeypatch):
    import tarso_pipeline.evt as evt

    monkeypatch.setattr(evt, "hessian_shape_se", lambda *a, **k: float("nan"))
    selection = evt.select_family(POOLED, replicates=20)
    assert selection.shape_se_method == "bootstrap (hessian unusable)"
    assert np.isfinite(selection.shape_se)


def test_a_given_se_is_used_as_is():
    # A huge SE makes any shape unresolved -> exp; a tiny one resolves it -> gev.
    assert select_family(POOLED, shape_se=10.0).family == "exp"
    assert select_family(POOLED, shape_se=1e-6).family == "gev"


def test_an_unknown_se_method_is_rejected():
    with pytest.raises(ValueError, match="se_method"):
        select_family(POOLED, se_method="jackknife")


# --- the bootstrap -----------------------------------------------------------


def test_bootstrap_fits_are_reproducible_and_shaped():
    first = bootstrap_gev_params(POOLED, replicates=15, seed=4)
    second = bootstrap_gev_params(POOLED, replicates=15, seed=4)
    assert first.shape == (15, 3)
    assert np.allclose(first, second)


def test_vectorised_levels_match_return_level():
    params = genextreme.fit(POOLED)
    periods = (2, 10, 100)
    for lam in (1.0, 0.361):
        expected = [return_level(params, "gev", T, lam) for T in periods]
        assert np.allclose(gev_levels(params, periods, lam), expected, equal_nan=True)
    stacked = gev_levels(np.array([params, params]), periods)
    assert stacked.shape == (2, 3)


def test_levels_below_the_trim_threshold_are_nan():
    assert np.isnan(gev_levels((0.0, 0.0, 1.0), (2,), lam=0.361)[0])


def test_the_band_brackets_the_central_level():
    params = genextreme.fit(POOLED)
    fits = bootstrap_gev_params(POOLED, replicates=60, start=params)
    low, high = gev_band(fits, (10, 100))
    central = gev_levels(params, (10, 100))
    assert np.all(low <= central) and np.all(central <= high)
    assert (high - low)[1] > (high - low)[0]  # wider further out


def test_tail_uncertainty_reports_both_ways_in_head_units():
    result = tail_uncertainty(POOLED, "dry", t_list=(10, 100), replicates=40)
    summary = result["summary"]
    assert summary["n_resamples"] == 40
    assert summary["family_hessian"] in ("exp", "gev")
    assert summary["family_bootstrap"] in ("exp", "gev")
    levels = result["levels"]
    # A dry level in head units sits below the pooled minima's median, and the
    # sign flip must not leave the bounds swapped.
    assert (levels["ci_low"] <= levels["level"]).all()
    assert (levels["level"] <= levels["ci_high"]).all()
    assert levels.loc[100, "level"] < np.median(POOLED)


def test_model_uncertainty_rows_match_the_table_columns(tmp_path):
    for tail, column in (("dry", "head_min"), ("wet", "head_max")):
        pd.DataFrame({column: POOLED, "year": 1956}).to_csv(
            tmp_path / f"series_abc_{tail}.csv")
    assert pooled_series(tmp_path) == ["series_abc"]
    pooled = read_pooled(tmp_path, "series_abc")
    shapes, levels, per_tail = model_uncertainty(
        pooled, "abc" * 10, "series_abc", "run_1", t_list=(10, 100), replicates=15,
    )
    assert set(per_tail) == {"dry", "wet"}
    assert len(shapes) == 2 and len(levels) == 4
    assert set(shapes[0]) == set(SHAPE_UNCERTAINTY_COLUMNS)
    assert set(levels[0]) == set(RETURN_LEVEL_CI_COLUMNS)
