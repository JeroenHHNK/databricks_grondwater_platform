import sqlite3

import pandas as pd
import pytest

from tarso_pipeline import results

T = "waterkeringbeheer_prd.jvanruitenbeek.valid_pastas_fit_metrics"


def test_latest_run_sql_returns_the_last_run_per_series():
    # Plain SQL: runs the same on sqlite, with the three-part name swapped for a local table.
    db = sqlite3.connect(":memory:")
    pd.DataFrame({"meetreeks_id": ["a", "a", "b", "a"], "model_type": ["tarso", "tarso", "tarso", "recharge"],
                  "run_id": ["20260101T000000Z", "20260201T000000Z", "20260101T000000Z", "20250101T000000Z"],
                  "r2": [0.5, 0.8, 0.7, 0.6]}).to_sql("m", db)
    out = pd.read_sql(results.latest_run_sql(T).replace(T, "m"), db)
    assert sorted(zip(out["meetreeks_id"], out["model_type"], out["r2"])) == [
        ("a", "recharge", 0.6), ("a", "tarso", 0.8), ("b", "tarso", 0.7)]
    one = pd.read_sql(results.latest_run_sql(T, meetreeks_id="a").replace(T, "m"), db)
    assert set(one["meetreeks_id"]) == {"a"} and len(one) == 2


def test_latest_run_sql_rejects_injection():
    with pytest.raises(ValueError):
        results.latest_run_sql(T, meetreeks_id="x' OR 1=1 --")
    with pytest.raises(ValueError):
        results.latest_run_sql("drop table x")


def test_latest_rows_keeps_the_last_run_per_series():
    frame = pd.DataFrame({"meetreeks_id": ["a", "a", "b"], "model_type": "tarso",
                          "run_id": ["20260101T000000Z", "20260201T000000Z", "20260101T000000Z"],
                          "r2": [0.5, 0.8, 0.7]})
    out = results.latest_rows(frame)
    assert sorted(zip(out["meetreeks_id"], out["r2"])) == [("a", 0.8), ("b", 0.7)]


def test_picks_are_saved_with_their_threshold_and_replaced_not_added(tmp_path):
    choice = results.read_family_choice(tmp_path / "none.csv")
    assert choice.empty and list(choice.columns) == results.FAMILY_CHOICE_COLUMNS
    choice = results.set_family_choice(choice, meetreeks_id="a", series_name="s", model_type="tarso",
                                       tail="wet", family="gpd", n_kept=200, note="vlakt af")
    choice = results.set_family_choice(choice, meetreeks_id="a", series_name="s", model_type="tarso",
                                       tail="wet", family="exp")       # replaces, not adds
    assert len(choice) == 1 and choice["n_kept"].iloc[0] == ""
    path = results.family_choice_path(tmp_path, "validated")
    assert path.name == "valid_pastas_family_choice.csv"
    results.write_family_choice(path, choice)
    assert results.read_family_choice(path)["family"].tolist() == ["exp"]
    with pytest.raises(ValueError):            # only gpd / exp can be picked
        results.set_family_choice(choice, meetreeks_id="a", series_name="s", model_type="tarso",
                                  tail="wet", family="gev")
    choice = results.set_family_choice(choice, meetreeks_id="a", series_name="s", model_type="tarso",
                                       tail="wet", family=None)
    assert choice.empty


def _levels():
    rows = []
    for T in (10, 100, 1000):
        rows.append({"family": "emp", "return_period": T, "level": T * 0.001, "n_kept": 704, "default_kept": True})
        rows.append({"family": "gev", "return_period": T, "level": T * 0.002, "n_kept": 704, "default_kept": True})
        for kept in (100, 254):
            for family, factor in (("gpd", 0.003), ("exp", 0.004)):
                rows.append({"family": family, "return_period": T, "level": T * factor + kept * 1e-6,
                             "n_kept": kept, "default_kept": kept == 254})
    return pd.DataFrame(rows).assign(meetreeks_id="a", model_type="tarso", tail="wet")


def _pick(family, n_kept=""):
    return pd.DataFrame([{"meetreeks_id": "a", "series_name": "s", "model_type": "tarso", "tail": "wet",
                          "family": family, "n_kept": n_kept, "note": "", "chosen_by": "", "chosen_at": ""}])


def test_up_to_t100_the_empirical_level_applies_past_it_the_picked_family():
    uit = results.chosen_levels(_levels(), _pick("gpd")).set_index("return_period")
    assert uit.loc[10, "family"] == "emp" and uit.loc[100, "family"] == "emp"
    assert uit.loc[1000, "family"] == "gpd" and uit.loc[1000, "n_kept"] == 254   # the default threshold
    assert uit["bron"].to_dict() == {10: results.EMPIRISCH, 100: results.EMPIRISCH, 1000: results.FAMILIE}


def test_a_picked_threshold_selects_its_levels():
    uit = results.chosen_levels(_levels(), _pick("exp", "100"))
    assert uit[uit["return_period"] == 1000][["family", "n_kept"]].values.tolist() == [["exp", 100]]


def test_no_pick_or_an_old_gev_pick_gives_nothing():
    assert results.chosen_levels(_levels(), _pick("gev")).empty
    assert results.chosen_levels(_levels(), results.read_family_choice("missing.csv")).empty


def test_levels_without_a_threshold_column_still_work():
    old = _levels()
    old = old[old["default_kept"]].drop(columns=["n_kept", "default_kept"])
    uit = results.chosen_levels(old, _pick("gpd"))
    assert sorted(uit["return_period"]) == [10, 100, 1000]


# --- the extremes plots in the export -------------------------------------------


def _png(path, payload: bytes, mtime: float):
    import os

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    os.utime(path, (mtime, mtime))
    return path


def test_the_newest_plot_per_model_and_kind_wins(tmp_path):
    from tarso_pipeline.results import latest_plots

    old = _png(tmp_path / "run_26_11_01" / "return_plots_fitted" / "series_a_return_periods.png", b"old", 1000)
    new = _png(tmp_path / "run_26_11_12" / "return_plots_fitted" / "series_a_return_periods.png", b"new", 2000)
    qq = _png(tmp_path / "run_26_11_01" / "qq_plots" / "series_a_qq.png", b"qq", 1000)
    _png(tmp_path / "run_26_11_12" / "pas_files" / "series_a_tarso.png", b"not a plot", 3000)
    found = latest_plots(tmp_path)
    assert found == {("series_a", "return_periods"): new, ("series_a", "qq"): qq}
    assert old not in found.values()


def test_sync_keeps_one_png_per_model_and_kind_and_leaves_unchanged_files(tmp_path):
    from tarso_pipeline.results import plot_path, plots_dir, sync_plots

    volume, repo = tmp_path / "volume", tmp_path / "repo"
    _png(volume / "run_1" / "return_plots_fitted" / "series_a_return_periods.png", b"a1", 1000)
    _png(volume / "run_2" / "return_plots_fitted" / "series_a_return_periods.png", b"a2", 2000)
    _png(volume / "run_1" / "confidence_plots" / "series_b_return_level.png", b"b", 1000)
    target = plots_dir(repo, "validated")
    _png(target / "series_gone_qq.png", b"stale", 500)

    assert sync_plots(volume, target) == {"copied": 2, "unchanged": 0, "removed": 1}
    assert plot_path(repo, "validated", "series_a", "return_periods").read_bytes() == b"a2"
    assert plot_path(repo, "validated", "series_b", "confidence").read_bytes() == b"b"
    assert sorted(p.name for p in target.glob("*.png")) == [
        "series_a_return_periods.png", "series_b_confidence.png"]
    assert sync_plots(volume, target) == {"copied": 0, "unchanged": 2, "removed": 0}


def test_a_missing_volume_folder_exports_nothing(tmp_path):
    from tarso_pipeline.results import latest_plots

    assert latest_plots(tmp_path / "nope") == {}


def test_plot_path_rejects_an_unknown_kind(tmp_path):
    from tarso_pipeline.results import plot_path

    with pytest.raises(ValueError, match="kind"):
        plot_path(tmp_path, "validated", "series_a", "histogram")


def test_the_plots_are_kept_per_model_type(tmp_path):
    from tarso_pipeline.results import latest_plots

    results = _png(tmp_path / "run_1" / "results_files" / "series_a_tarso_results.png", b"r", 1000)
    gamma = _png(tmp_path / "run_1" / "results_files" / "series_a_linear_gamma_results.png", b"g", 1000)
    diagnostics = _png(tmp_path / "run_1" / "diagnostics_files" / "series_a_tarso_diagnostics.png", b"d", 1000)
    found = latest_plots(tmp_path)
    assert found[("series_a_tarso", "results")] == results
    assert found[("series_a_linear_gamma", "results")] == gamma
    assert found[("series_a_tarso", "diagnostics")] == diagnostics


def test_find_plot_falls_back_to_the_old_tarso_name(tmp_path):
    from tarso_pipeline.results import find_plot, plots_dir

    target = plots_dir(tmp_path, "validated")
    old = _png(target / "series_a_return_periods.png", b"old", 1000)
    assert find_plot(tmp_path, "validated", "series_a", "tarso", "return_periods") == old
    assert find_plot(tmp_path, "validated", "series_a", "linear_gamma", "return_periods") is None
    new = _png(target / "series_a_tarso_return_periods.png", b"new", 2000)
    assert find_plot(tmp_path, "validated", "series_a", "tarso", "return_periods") == new


def test_syncing_some_kinds_leaves_the_others_alone(tmp_path):
    from tarso_pipeline.results import sync_plots

    volume, target = tmp_path / "volume", tmp_path / "plots"
    _png(volume / "run_1" / "qq_plots" / "series_a_qq.png", b"qq", 1000)
    _png(target / "series_a_results.png", b"kept", 500)
    _png(target / "series_gone_qq.png", b"stale", 500)
    assert sync_plots(volume, target, kinds=["qq"]) == {"copied": 1, "unchanged": 0, "removed": 1}
    assert (target / "series_a_results.png").read_bytes() == b"kept"
    with pytest.raises(ValueError, match="plot kind"):
        sync_plots(volume, target, kinds=["histogram"])
