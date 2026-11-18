import numpy as np
import pandas as pd
import pytest
from scipy.stats import genextreme

from tarso_pipeline.evt import (
    bootstrap_params,
    fit_family,
    gpd_shape_se,
    return_level,
    tail_threshold,
    trim_tail,
)
from tarso_pipeline.uncertainty import (
    RETURN_LEVEL_CI_COLUMNS,
    SHAPE_UNCERTAINTY_COLUMNS,
    band,
    family_levels,
    model_uncertainty,
    pooled_series,
    read_pooled,
    tail_uncertainty,
)

POOLED = genextreme.rvs(0.05, -2.0, 0.15, size=704, random_state=1)
KEPT, LAM = trim_tail(POOLED, 450)
U = tail_threshold(POOLED, 450)


def test_the_asymptotic_gpd_se_agrees_with_the_bootstrap():
    params = fit_family(KEPT, "gpd", U)
    fits = bootstrap_params(KEPT, "gpd", replicates=150, threshold=U)
    ratio = gpd_shape_se(params[0], len(KEPT)) / np.std(fits[:, 0])
    assert 0.7 < ratio < 1.4


def test_the_asymptotic_se_is_nan_where_it_does_not_hold():
    assert np.isnan(gpd_shape_se(-0.6, 254))
    assert gpd_shape_se(0.0, 400) < gpd_shape_se(0.0, 100)


def test_bootstrap_fits_keep_the_threshold_and_are_reproducible():
    first = bootstrap_params(KEPT, "gpd", replicates=15, seed=4, threshold=U)
    assert first.shape == (15, 3) and np.allclose(first[:, 1], U)
    assert np.allclose(first, bootstrap_params(KEPT, "gpd", replicates=15, seed=4, threshold=U))
    assert bootstrap_params(KEPT, "exp", replicates=5, threshold=U).shape == (5, 2)


@pytest.mark.parametrize("family", ["gpd", "exp", "gev"])
def test_vectorised_levels_match_return_level(family):
    params = fit_family(KEPT, family, None if family == "gev" else U)
    periods = (2, 10, 100)
    expected = [return_level(params, family, T, LAM) for T in periods]
    assert np.allclose(family_levels(params, family, periods, LAM), expected, equal_nan=True)
    assert family_levels(np.array([params, params]), family, periods, LAM).shape == (2, 3)


def test_the_band_brackets_the_central_level_and_widens():
    params = fit_family(KEPT, "gpd", U)
    fits = bootstrap_params(KEPT, "gpd", replicates=60, threshold=U)
    low, high = band(fits, "gpd", (10, 1000), LAM)
    central = family_levels(params, "gpd", (10, 1000), LAM)
    assert np.all(low <= central) and np.all(central <= high)
    assert (high - low)[1] > (high - low)[0]


def test_tail_uncertainty_gives_both_families_in_head_units():
    result = tail_uncertainty(POOLED, "dry", exclude_first=450, t_list=(10, 100), replicates=30)
    assert set(result["levels"]["family"]) == {"gpd", "exp"}
    levels = result["levels"]
    # The sign flip of the dry tail must not leave the bounds swapped.
    assert (levels["ci_low"] <= levels["level"]).all() and (levels["level"] <= levels["ci_high"]).all()
    assert result["summary"]["n_kept"] == 254 and result["summary"]["n_resamples"] == 30


def test_model_uncertainty_rows_match_the_table_columns(tmp_path):
    for tail, column in (("dry", "head_min"), ("wet", "head_max")):
        pd.DataFrame({column: POOLED, "year": 1956}).to_csv(tmp_path / f"series_abc_{tail}.csv")
    assert pooled_series(tmp_path) == ["series_abc"]
    shapes, levels, per_tail = model_uncertainty(
        read_pooled(tmp_path, "series_abc"), "abc" * 10, "series_abc", "run_1",
        exclude_first=450, t_list=(10, 100), replicates=15,
    )
    assert set(per_tail) == {"dry", "wet"}
    assert len(shapes) == 2 and len(levels) == 2 * 2 * 2      # tails x families x T
    assert set(shapes[0]) == set(SHAPE_UNCERTAINTY_COLUMNS)
    assert set(levels[0]) == set(RETURN_LEVEL_CI_COLUMNS)
