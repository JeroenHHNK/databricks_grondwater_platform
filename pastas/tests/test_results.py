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
