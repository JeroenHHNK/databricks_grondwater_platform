import numpy as np
import pytest
from scipy.stats import genextreme

from tarso_pipeline.evt import (
    FAMILIES,
    MIN_BLOCKS,
    MIN_SPREAD,
    as_maxima,
    empirical_points,
    exceedance_probability,
    fit_all_families,
    fit_family,
    from_maxima,
    return_level,
    select_family,
    trim_tail,
)

SAMPLE = genextreme.rvs(0.05, 0, 1, size=704, random_state=1)


# --- both tails are maxima -------------------------------------------------


def test_a_drought_minimum_is_a_maximum_of_minus_head():
    head = np.array([-2.0, -3.0, -1.0])
    assert as_maxima(head, "dry").max() == 3.0
    assert as_maxima(head, "wet").max() == -1.0


def test_the_sign_flip_round_trips():
    head = np.random.default_rng(0).normal(-2, 0.3, 100)
    assert np.allclose(from_maxima(as_maxima(head, "dry"), "dry"), head)
    assert np.allclose(from_maxima(as_maxima(head, "wet"), "wet"), head)


def test_an_unknown_tail_is_rejected():
    with pytest.raises(ValueError, match="dry.*wet"):
        as_maxima(np.array([1.0]), "damp")


# --- trimming and the lam correction ---------------------------------------


def test_trimming_drops_the_least_extreme_and_reports_lam():
    kept, lam = trim_tail(SAMPLE, 450)
    assert len(kept) == 254
    assert lam == pytest.approx(254 / 704)
    assert kept.min() >= np.sort(SAMPLE)[449]


def test_no_trimming_leaves_lam_at_one():
    kept, lam = trim_tail(SAMPLE, 0)
    assert len(kept) == 704 and lam == 1.0


def test_trimming_everything_is_rejected():
    with pytest.raises(ValueError, match="would leave nothing"):
        trim_tail(SAMPLE, 704)


def test_the_correction_changes_the_exceedance_probability():
    assert exceedance_probability(100, 1.0) == pytest.approx(0.01)
    assert exceedance_probability(100, 0.36) == pytest.approx(1 / 36)


def test_the_uncorrected_level_is_materially_more_extreme():
    # The bug the spec warns about: without lam, trimmed levels drift out.
    kept, lam = trim_tail(SAMPLE, 450)
    params = fit_family(kept, "gev")
    corrected = return_level(params, "gev", 100, lam)
    naive = return_level(params, "gev", 100, 1.0)
    assert naive > corrected


def test_a_return_period_below_the_trim_threshold_is_nan():
    # With lam = 0.361 the smallest computable T is 1/lam = 2.77, so T=2 cannot
    # be recovered from the trimmed sample and must not be invented.
    kept, lam = trim_tail(SAMPLE, 450)
    params = fit_family(kept, "gev")
    assert np.isnan(return_level(params, "gev", 2, lam))
    assert np.isfinite(return_level(params, "gev", 5, lam))


def test_the_whole_table_is_nan_at_t2_when_trimmed():
    _, levels = fit_all_families(SAMPLE, exclude_first=450, t_list=(2, 10))
    assert levels.loc[2].isna().all()
    assert levels.loc[10].notna().all()


# --- empirical plotting positions ------------------------------------------


def test_ranks_are_computed_on_the_untrimmed_sample():
    full = empirical_points(SAMPLE, 0)
    trimmed = empirical_points(SAMPLE, 450)
    assert len(full) == 704 and len(trimmed) == 254
    # The largest value keeps return period (n+1)/1 either way.
    assert full["return_period"].max() == trimmed["return_period"].max() == 705


def test_the_trimmed_body_is_dropped_not_renumbered():
    trimmed = empirical_points(SAMPLE, 450)
    assert trimmed["rank"].max() == 254
    assert trimmed["return_period"].min() == pytest.approx(705 / 254)


# --- family selection ------------------------------------------------------


@pytest.mark.parametrize(
    "shape,expected_family,expected_verdict",
    [(0.0, "exp", "gumbel"), (0.3, "gev", "weibull"), (-0.3, "gev", "frechet")],
)
def test_known_shapes_get_the_right_verdict(shape, expected_family, expected_verdict):
    sample = genextreme.rvs(shape, 0, 1, size=704, random_state=7)
    selection = select_family(sample, replicates=40)
    assert selection.family == expected_family
    assert selection.verdict == expected_verdict


def test_frechet_is_flagged_but_gumbel_and_weibull_are_not():
    assert select_family(genextreme.rvs(-0.3, 0, 1, size=704, random_state=7),
                         replicates=40).review_flag is True
    assert select_family(genextreme.rvs(0.0, 0, 1, size=704, random_state=7),
                         replicates=40).review_flag is False


def test_too_few_blocks_is_insufficient_and_flagged():
    selection = select_family(SAMPLE[:50], replicates=20)
    assert selection.verdict == "insufficient"
    assert selection.family is None and selection.review_flag


def test_a_capped_head_is_degenerate_not_a_clean_fit():
    # The Tarso failure mode: dmax caps the head and all 704 maxima are equal.
    # A GEV fits that perfectly cleanly and meaninglessly.
    capped = np.full(704, -1.2345) + np.random.default_rng(0).normal(0, 1e-6, 704)
    selection = select_family(capped, replicates=20)
    assert selection.verdict == "degenerate"
    assert selection.family is None and selection.review_flag
    assert np.ptp(capped) < MIN_SPREAD


def test_an_unresolved_shape_chooses_the_exponential():
    selection = select_family(genextreme.rvs(0.0, 0, 1, size=704, random_state=3),
                              replicates=60)
    assert selection.family == "exp"
    assert abs(selection.shape) < 2 * selection.shape_se


def test_the_selection_records_why():
    selection = select_family(SAMPLE, replicates=40)
    row = selection.as_row()
    assert set(row) >= {"chosen_family", "verdict", "shape", "shape_se",
                        "n_blocks", "review_flag", "reason"}
    assert row["reason"]


def test_min_blocks_matches_the_spec():
    assert MIN_BLOCKS == 100
    assert MIN_SPREAD == 1e-3


# --- all three families ----------------------------------------------------


def test_all_three_families_are_fitted_and_reported():
    params, levels = fit_all_families(SAMPLE, 0, (10, 100))
    assert set(levels.columns) == set(FAMILIES)
    for family in FAMILIES:
        assert params[family] is not None


def test_gpd_is_fitted_but_never_recommended():
    from tarso_pipeline.evt import RECOMMENDABLE

    assert "gpd" in FAMILIES
    assert "gpd" not in RECOMMENDABLE
    for shape in (-0.3, 0.0, 0.3):
        sample = genextreme.rvs(shape, 0, 1, size=704, random_state=5)
        assert select_family(sample, replicates=20).family != "gpd"
