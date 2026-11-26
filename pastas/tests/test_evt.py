import numpy as np
import pytest
from scipy.stats import genextreme

from tarso_pipeline.evt import (
    FAMILIES,
    MIN_BLOCKS,
    MIN_SPREAD,
    as_maxima,
    diagnose,
    empirical_points,
    exceedance_probability,
    fit_all_families,
    fit_family,
    from_maxima,
    return_level,
    tail_threshold,
    threshold_scan,
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
    params = fit_family(kept, "gpd", tail_threshold(SAMPLE, 450))
    corrected = return_level(params, "gpd", 100, lam)
    naive = return_level(params, "gpd", 100, 1.0)
    assert naive > corrected


def test_a_return_period_below_the_trim_threshold_is_nan():
    # With lam = 0.361 the smallest computable T is 1/lam = 2.77, so T=2 cannot
    # be recovered from the trimmed sample and must not be invented.
    kept, lam = trim_tail(SAMPLE, 450)
    params = fit_family(kept, "gpd", tail_threshold(SAMPLE, 450))
    assert np.isnan(return_level(params, "gpd", 2, lam))
    assert np.isfinite(return_level(params, "gpd", 5, lam))


def test_the_trimmed_families_are_nan_at_t2_but_the_gev_on_all_years_is_not():
    _, levels = fit_all_families(SAMPLE, exclude_first=450, t_list=(2, 10))
    assert levels.loc[2, ["gpd", "exp"]].isna().all()
    assert np.isfinite(levels.loc[2, "gev"])
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


# --- the threshold and the families ------------------------------------------


def test_the_threshold_sits_between_the_last_dropped_and_the_first_kept_value():
    ordered = np.sort(SAMPLE)
    assert tail_threshold(SAMPLE, 450) == pytest.approx((ordered[449] + ordered[450]) / 2)
    assert tail_threshold(SAMPLE, 0) == ordered[0]


def test_gpd_and_exp_start_at_the_threshold_and_gev_uses_every_year():
    params, _ = fit_all_families(SAMPLE, exclude_first=450, t_list=(10, 100))
    u = tail_threshold(SAMPLE, 450)
    assert params["gpd"]["params"][1] == pytest.approx(u)       # (shape, loc, scale)
    assert params["exp"]["params"][0] == pytest.approx(u)       # (loc, scale)
    assert params["gpd"]["n_fitted"] == params["exp"]["n_fitted"] == 254
    assert params["gev"]["n_fitted"] == 704 and params["gev"]["lam"] == 1.0


@pytest.mark.parametrize("shape", [0.3, 0.0])
def test_the_trimmed_gpd_recovers_the_t1000_level_where_a_trimmed_gev_overshoots(shape):
    # The reason for the GPD: on the top of a GEV sample a GPD is right, a GEV is not.
    from scipy.stats import genextreme

    true = genextreme.ppf(1 - 1 / 1000, shape, 0, 0.15)
    errors = {"gpd": [], "gev_trimmed": []}
    for seed in range(40):
        x = genextreme.rvs(shape, 0, 0.15, size=704, random_state=seed)
        kept, lam = trim_tail(x, 450)
        errors["gpd"].append(return_level(fit_family(kept, "gpd", tail_threshold(x, 450)), "gpd", 1000, lam) - true)
        errors["gev_trimmed"].append(return_level(fit_family(kept, "gev"), "gev", 1000, lam) - true)
    assert abs(np.mean(errors["gpd"])) < 0.08         # ~3 cm bias against ~11 cm spread
    assert np.mean(errors["gev_trimmed"]) > 0.15


def test_the_threshold_scan_has_a_row_per_threshold_with_shape_and_levels():
    scan = threshold_scan(SAMPLE, (100, 254, 400, 9999), (100, 1000))
    assert scan["n_kept"].tolist() == [100, 254, 400]           # more than the sample is skipped
    assert (scan["exclude_first"] + scan["n_kept"] == 704).all()
    assert {"gpd_shape", "gpd_shape_se", "gpd_T1000", "exp_T1000", "threshold"} <= set(scan.columns)
    assert scan["threshold"].is_monotonic_decreasing              # more kept = lower threshold
    assert scan["gpd_shape_se"].is_monotonic_decreasing           # more kept = more certain


def test_the_default_threshold_snaps_to_the_nearest_scanned_one():
    from tarso_pipeline.evt import snap_exclude_first

    assert snap_exclude_first(704, 450, (100, 250, 400)) == 454
    assert snap_exclude_first(704, 450, (100, 254)) == 450
    assert snap_exclude_first(704, 450, ()) == 450
    assert snap_exclude_first(120, 20, (100, 150)) == 20        # 150 is more than there is


def test_too_few_blocks_and_a_capped_head_are_flagged():
    assert diagnose(SAMPLE[:50])["flag"] == "insufficient"
    capped = np.full(704, -1.2345) + np.random.default_rng(0).normal(0, 1e-6, 704)
    assert np.ptp(capped) < MIN_SPREAD
    assert diagnose(capped)["flag"] == "degenerate"
    assert diagnose(SAMPLE, 450)["flag"] == ""


def test_min_blocks_matches_the_spec():
    assert MIN_BLOCKS == 100
    assert MIN_SPREAD == 1e-3


def test_all_three_families_are_fitted_and_reported():
    params, levels = fit_all_families(SAMPLE, 450, (10, 100))
    assert set(levels.columns) == set(FAMILIES)
    for family in FAMILIES:
        assert params[family] is not None


def test_return_level_rows_cover_every_threshold_of_the_scan():
    from tarso_pipeline.evt_report import RETURN_LEVEL_COLUMNS, return_level_rows, tail_tables

    per_tail = {"wet": tail_tables(SAMPLE, "wet", exclude_first=450, t_list=(10, 1000), kept_grid=(100, 250, 400))}
    rows = return_level_rows("id", "series_x", "run", per_tail)
    assert list(rows[0]) == RETURN_LEVEL_COLUMNS
    kept = {(r["family"], r["n_kept"]) for r in rows}
    assert kept == {("emp", 704), ("gev", 704), *((f, k) for f in ("gpd", "exp") for k in (100, 250, 400))}
    default = [r for r in rows if r["family"] == "gpd" and r["default_kept"]]
    assert {r["n_kept"] for r in default} == {250}                # 704 - 450 = 254, snapped to 250
    assert [r["level"] for r in rows if r["family"] == "emp" and r["return_period"] == 1000] == [None]
    assert return_level_rows("id", "s", "run", {}) == []


def test_the_dry_tail_comes_back_in_head_units():
    from tarso_pipeline.evt_report import tail_tables

    head = -2.0 - np.abs(SAMPLE) * 0.1                            # minima of a head around -2 m
    data = tail_tables(head, "dry", exclude_first=450, t_list=(10, 100))
    assert (data["levels"]["emp"] < -2.0).all()
    assert data["levels"].loc[100, "gpd"] < data["levels"].loc[10, "gpd"]   # rarer = lower
    assert data["threshold"] < -2.0
