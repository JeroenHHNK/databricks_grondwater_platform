"""The per-series model pick: the stamp, the runaway check, AIC with parsimony, the holdout."""

import pandas as pd
import pytest

from tarso_pipeline.extremes_run import split_stem
from tarso_pipeline.selection import (
    check_summary,
    model_in_use,
    read_model_choice,
    runaway,
    select_models,
    set_model_choice,
    write_model_choice,
)

STAMP = ("rsq>=0.7", "t95_Recharge < 0.5 Δt_calib", "|Recharge_A| > 1.96σ", "Bounds: Recharge_A")


def _checks(series, model_type, failed=(), runs=False):
    rows = [{"meetreeks_id": series, "model_type": model_type, "check": c, "passed": c not in failed}
            for c in STAMP]
    rows.append({"meetreeks_id": series, "model_type": model_type, "check": "runs_test (p > α)", "passed": runs})
    return rows


def _fit(series, model_type, aic, k, r2=0.8, holdout=0.6, lo=-3.0, hi=-1.0):
    return {"run_id": "r1", "meetreeks_id": series, "series_name": f"series_{series}", "model_type": model_type,
            "success": True, "aic": aic, "n_param": k, "r2": r2, "r2_holdout": holdout,
            "min_value": lo, "max_value": hi}


def test_check_summary_gives_the_stamp_without_the_runs_test():
    rows = _checks("a", "tarso") + _checks("a", "linear_exp", failed=("Bounds: Recharge_A",))
    out = check_summary(pd.DataFrame(rows)).set_index("model_type")
    assert out.loc["tarso", "passed"] and not out.loc["tarso", "pass_runs"]
    assert not out.loc["linear_exp", "passed"] and not out.loc["linear_exp", "pass_bounds"]


def test_a_checklist_error_passes_nothing():
    rows = [{"meetreeks_id": "a", "model_type": "tarso", "check": "checklist_error", "passed": None}]
    out = check_summary(pd.DataFrame(rows))
    assert not out["passed"].iloc[0]


@pytest.mark.parametrize("dry, wet, expected", [(-4.0, -0.5, False), (-4.6, -0.5, True), (-4.0, 0.6, True),
                                                (None, None, False)])
def test_runaway_is_t100_more_than_one_and_a_half_metre_outside_the_range(dry, wet, expected):
    assert runaway(dry, wet, -3.0, -1.0) is expected


def test_the_lowest_aic_among_the_passed_models_wins():
    metrics = pd.DataFrame([_fit("a", "tarso", -100, 7), _fit("a", "linear_gamma", -150, 5),
                            _fit("a", "linear_fourparam", -300, 6)])
    checks = pd.DataFrame(_checks("a", "tarso") + _checks("a", "linear_gamma")
                          + _checks("a", "linear_fourparam", failed=("Bounds: Recharge_A",)))
    out = select_models(metrics, checks).set_index("model_type")
    assert out["selected"].sum() == 1 and out.loc["linear_gamma", "selected"]   # fourparam has lower AIC, fails
    assert out.loc["linear_gamma", "tier"] == "passed"
    assert out.loc["linear_gamma", "d_aic_tarso"] == -50


def test_a_simpler_model_within_two_aic_wins():
    metrics = pd.DataFrame([_fit("a", "linear_exp", -199, 4), _fit("a", "linear_dexp", -200.5, 6)])
    checks = pd.DataFrame(_checks("a", "linear_exp") + _checks("a", "linear_dexp"))
    out = select_models(metrics, checks).set_index("model_type")
    assert out.loc["linear_exp", "selected"] and "minder parameters" in out.loc["linear_exp", "reason"]


def test_without_a_passed_model_the_best_effort_is_picked():
    metrics = pd.DataFrame([_fit("a", "tarso", -100, 7), _fit("a", "linear_gamma", -150, 5)])
    checks = pd.DataFrame(_checks("a", "tarso", failed=("rsq>=0.7",))
                          + _checks("a", "linear_gamma", failed=("rsq>=0.7",)))
    out = select_models(metrics, checks).set_index("model_type")
    assert out.loc["linear_gamma", "selected"] and out.loc["linear_gamma", "tier"] == "best_effort"


def test_a_runaway_model_is_never_picked():
    metrics = pd.DataFrame([_fit("a", "tarso", -100, 7), _fit("a", "linear_gamma", -150, 5)])
    checks = pd.DataFrame(_checks("a", "tarso") + _checks("a", "linear_gamma"))
    extremes = pd.DataFrame({"meetreeks_id": "a", "model_type": ["tarso", "linear_gamma"],
                             "dr_emp_T100": [-3.5, -3.4], "wet_emp_T100": [-0.8, 12.0]})
    out = select_models(metrics, checks, extremes).set_index("model_type")
    assert out.loc["tarso", "selected"] and out.loc["linear_gamma", "runaway"]


def test_a_lower_aic_with_a_much_worse_holdout_loses():
    metrics = pd.DataFrame([_fit("a", "tarso", -100, 7, holdout=0.60),
                            _fit("a", "linear_fourparam", -400, 6, holdout=0.40)])
    checks = pd.DataFrame(_checks("a", "tarso") + _checks("a", "linear_fourparam"))
    out = select_models(metrics, checks).set_index("model_type")
    assert out.loc["tarso", "selected"] and "holdout" in out.loc["tarso", "reason"]


def test_failed_fits_are_left_out():
    metrics = pd.DataFrame([_fit("a", "tarso", -100, 7),
                            {**_fit("a", "linear_gamma", None, None), "success": False}])
    out = select_models(metrics, pd.DataFrame(_checks("a", "tarso")))
    assert out["model_type"].tolist() == ["tarso"] and out["selected"].all()


def test_a_person_overrules_the_automatic_pick(tmp_path):
    selection = pd.DataFrame({"run_id": "r2", "meetreeks_id": ["a", "a", "b"],
                              "model_type": ["tarso", "linear_gamma", "tarso"], "selected": [False, True, True]})
    path = tmp_path / "valid_pastas_model_choice.csv"
    choice = set_model_choice(read_model_choice(path), meetreeks_id="b", series_name="series_b",
                              model_type="linear_dexp", note="wet tail")
    write_model_choice(path, choice)
    used = model_in_use(selection, read_model_choice(path), series=pd.Series(["a", "b", "c"])).set_index("meetreeks_id")
    assert used.loc["a"].tolist() == ["linear_gamma", "automatisch"]
    assert used.loc["b"].tolist() == ["linear_dexp", "handmatig"]
    assert used.loc["c"].tolist() == ["tarso", "standaard"]
    back = set_model_choice(read_model_choice(path), meetreeks_id="b", series_name="series_b", model_type=None)
    assert back.empty
    with pytest.raises(ValueError, match="model_type"):
        set_model_choice(back, meetreeks_id="b", series_name="s", model_type="berendrecht_exp")


def test_only_the_latest_selection_run_of_a_series_counts():
    selection = pd.DataFrame({"run_id": ["r1", "r1", "r2", "r2"], "meetreeks_id": "a",
                              "model_type": ["tarso", "linear_gamma", "tarso", "linear_gamma"],
                              "selected": [False, True, True, False]})
    assert model_in_use(selection)["model_type"].tolist() == ["tarso"]


@pytest.mark.parametrize("stem, expected", [
    ("series_abc_tarso", ("series_abc", "tarso")),
    ("series_abc_linear_gamma", ("series_abc", "linear_gamma")),
    ("series_abc_linear_fourparam", ("series_abc", "linear_fourparam")),
    ("series_abc", ("series_abc", "tarso")),          # a file from before the model type was in the name
])
def test_split_stem(stem, expected):
    assert split_stem(stem) == expected


# --- the holdout rules of config/model_settings.toml -------------------------------------

from tarso_pipeline.settings import SelectionSettings  # noqa: E402

OFF = SelectionSettings()  # only the Tarso rule, as before the settings file


def _three(bucket_holdout, plain_holdout, bucket_r2=0.82, plain_r2=0.80, tarso_holdout=0.10):
    metrics = pd.DataFrame([_fit("a", "tarso", -100, 7, r2=0.70, holdout=tarso_holdout),
                            _fit("a", "linear_dexp_lake_baro_radar", -300, 8, r2=plain_r2, holdout=plain_holdout),
                            _fit("a", "linear_dexp_lake_baro_bucket_radar", -400, 10, r2=bucket_r2,
                                 holdout=bucket_holdout)])
    checks = pd.DataFrame(sum((_checks("a", m) for m in metrics["model_type"]), []))
    return metrics, checks


def _picked(metrics, checks, rules, **kw):
    out = select_models(metrics, checks, rules=rules, **kw)
    return out[out["selected"]].iloc[0]


def test_a_bucket_model_must_not_predict_worse_than_without_the_bucket():
    metrics, checks = _three(bucket_holdout=0.72, plain_holdout=0.85)
    assert _picked(metrics, checks, OFF)["model_type"] == "linear_dexp_lake_baro_bucket_radar"
    rules = SelectionSettings(holdout_vs_without_bucket=0.05)
    pick = _picked(metrics, checks, rules)
    assert pick["model_type"] == "linear_dexp_lake_baro_radar" and "zonder bakje" in pick["reason"]
    # within the tolerance the lower AIC still wins
    metrics, checks = _three(bucket_holdout=0.82, plain_holdout=0.85)
    assert _picked(metrics, checks, rules)["model_type"] == "linear_dexp_lake_baro_bucket_radar"


def test_a_candidate_far_below_the_best_holdout_is_dropped():
    metrics, checks = _three(bucket_holdout=0.06, plain_holdout=0.51)  # Tarso's 0.10 - 0.05 lets it through
    assert _picked(metrics, checks, OFF)["model_type"] == "linear_dexp_lake_baro_bucket_radar"  # Tarso's is low
    pick = _picked(metrics, checks, SelectionSettings(holdout_gap=0.10))
    assert pick["model_type"] == "linear_dexp_lake_baro_radar" and "de beste" in pick["reason"]


def test_a_better_holdout_cannot_buy_a_model_that_no_longer_fits():
    metrics, checks = _three(bucket_holdout=-2.9, plain_holdout=-1.7, bucket_r2=0.89, plain_r2=0.05,
                             tarso_holdout=-8.0)  # Tarso no better: only the R2 guard decides
    checks.loc[checks["check"].str.startswith("rsq"), "passed"] = False  # best effort, as in the run
    rules = SelectionSettings(holdout_gap=0.10)
    assert _picked(metrics, checks, rules)["model_type"] != "linear_dexp_lake_baro_bucket_radar"
    guarded = SelectionSettings(holdout_gap=0.10, max_r2_loss=0.10)
    assert _picked(metrics, checks, guarded)["model_type"] == "linear_dexp_lake_baro_bucket_radar"


def test_the_r2_guard_does_not_undo_the_tarso_rule():
    # every model predicts far worse than Tarso: the Tarso rule keeps Tarso, as before the guard
    metrics, checks = _three(bucket_holdout=-12.6, plain_holdout=-13.1, bucket_r2=0.75, plain_r2=0.56,
                             tarso_holdout=-7.8)
    metrics.loc[metrics["model_type"] == "tarso", "r2"] = 0.50
    rules = SelectionSettings(max_r2_loss=0.10, holdout_gap=0.10, holdout_vs_without_bucket=0.05)
    assert _picked(metrics, checks, rules)["model_type"] == "tarso"


def test_the_holdout_rules_never_leave_nothing():
    metrics, checks = _three(bucket_holdout=0.1, plain_holdout=0.1)
    pick = _picked(metrics, checks, SelectionSettings(min_holdout=0.9, holdout_gap=0.0))
    assert pick["model_type"] == "linear_dexp_lake_baro_bucket_radar"


def test_an_override_excludes_a_stress_and_flags_the_extremes(tmp_path):
    from tarso_pipeline.overrides import OVERRIDE_COLUMNS, read_overrides

    metrics, checks = _three(bucket_holdout=0.80, plain_holdout=0.80)
    path = tmp_path / "series_overrides.csv"
    pd.DataFrame([{"meetreeks_id": "a", "series_name": "series_a", "exclude_stresses": "bucket",
                   "trust_extremes": "nee", "note": "onttrekking #3 naast de buis"}]).reindex(
        columns=OVERRIDE_COLUMNS).to_csv(path, index=False)
    overrides = read_overrides(path)
    out = select_models(metrics, checks, rules=OFF, overrides=overrides).set_index("model_type")
    assert not out.loc["linear_dexp_lake_baro_bucket_radar", "eligible"]
    assert "uitgesloten" in out.loc["linear_dexp_lake_baro_bucket_radar", "reason"]
    assert out.loc["linear_dexp_lake_baro_radar", "selected"]
    assert out.loc["linear_dexp_lake_baro_radar", "reason"].endswith(
        "extremen niet betrouwbaar: onttrekking #3 naast de buis")


@pytest.mark.parametrize("row, match", [
    ({"exclude_stresses": "meer"}, "exclude_stresses"),
    ({"exclude_model_types": "linear_nonsense"}, "unknown exclude_model_types"),
])
def test_a_wrong_override_stops_with_the_row(tmp_path, row, match):
    from tarso_pipeline.overrides import read_overrides

    path = tmp_path / "o.csv"
    pd.DataFrame([{"meetreeks_id": "a", **row}]).to_csv(path, index=False)
    with pytest.raises(ValueError, match=match):
        read_overrides(path)


def test_overrides_by_stress_by_type_and_radar():
    from tarso_pipeline.overrides import OVERRIDE_COLUMNS, excludes

    o = pd.DataFrame([{"meetreeks_id": "a", "exclude_stresses": "radar;lake", "exclude_model_types": "tarso"}]
                     ).reindex(columns=OVERRIDE_COLUMNS).fillna("")
    assert excludes(o, "a", "tarso") and excludes(o, "a", "linear_exp_lake_baro")
    assert excludes(o, "a", "linear_exp_lake_baro_bucket_radar") and not excludes(o, "a", "linear_exp")
    assert not excludes(o, "b", "tarso") and not excludes(pd.DataFrame(), "a", "tarso")
