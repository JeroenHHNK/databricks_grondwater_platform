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


def selection():
    return pd.DataFrame({
        "meetreeks_id": ["a", "a", "b"], "model_type": "tarso", "tail": ["dry", "wet", "wet"],
        "chosen_family": ["gev", "exp", None], "verdict": ["weibull", "gumbel", "degenerate"]})


def test_a_pick_wins_and_removing_it_goes_back_to_automatic(tmp_path):
    choice = results.read_family_choice(tmp_path / "none.csv")
    assert choice.empty and list(choice.columns) == results.FAMILY_CHOICE_COLUMNS
    choice = results.set_family_choice(choice, meetreeks_id="a", series_name="s", model_type="tarso",
                                       tail="wet", family="gpd", note="langere staart")
    choice = results.set_family_choice(choice, meetreeks_id="a", series_name="s", model_type="tarso",
                                       tail="wet", family="gev")   # replaces, not adds
    assert len(choice) == 1

    path = results.family_choice_path(tmp_path, "validated")
    assert path.name == "valid_pastas_family_choice.csv"
    results.write_family_choice(path, choice)
    choice = results.read_family_choice(path)

    fam = results.effective_family(selection(), choice).set_index(["meetreeks_id", "tail"])
    assert fam.loc[("a", "wet"), ["family", "family_source"]].tolist() == ["gev", results.GEKOZEN]
    assert fam.loc[("a", "dry"), ["family", "family_source"]].tolist() == ["gev", results.AUTOMATISCH]
    assert pd.isna(fam.loc[("b", "wet"), "family"])          # degenerate: no family

    levels = pd.DataFrame({"meetreeks_id": "a", "model_type": "tarso", "tail": ["wet"] * 4,
                           "family": ["gev", "exp", "gpd", "emp"], "return_period": 10,
                           "level": [1.0, 2.0, 3.0, 4.0]})
    assert results.chosen_levels(levels, selection(), choice)["level"].tolist() == [1.0]

    choice = results.set_family_choice(choice, meetreeks_id="a", series_name="s", model_type="tarso",
                                       tail="wet", family=None)
    assert results.chosen_levels(levels, selection(), choice)["level"].tolist() == [2.0]   # automatic: exp
    with pytest.raises(ValueError):
        results.set_family_choice(choice, meetreeks_id="a", series_name="s", model_type="tarso",
                                  tail="wet", family="normal")


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


def test_the_fit_plots_are_found_without_their_model_type(tmp_path):
    from tarso_pipeline.results import latest_plots

    results = _png(tmp_path / "run_1" / "results_files" / "series_a_tarso_results.png", b"r", 1000)
    diagnostics = _png(tmp_path / "run_1" / "diagnostics_files" / "series_a_tarso_diagnostics.png", b"d", 1000)
    found = latest_plots(tmp_path)
    assert found[("series_a", "results")] == results
    assert found[("series_a", "diagnostics")] == diagnostics


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
