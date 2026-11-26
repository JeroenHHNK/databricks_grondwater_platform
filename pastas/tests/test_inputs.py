"""Forcing sources and extra stresses per model type (``tarso_pipeline.inputs``)."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from tarso_pipeline import inputs
from tarso_pipeline.fit import EXTENDED_MODEL_TYPES, MODEL_LABELS, MODEL_SPECS, model_spec
from tarso_pipeline.inputs import InputLibrary, monthly_factors, polder_of, scale_members, source_label

DAYS = pd.date_range("2018-01-01", "2026-06-30", freq="D")


def _write_forcing(folder):
    rng = np.random.default_rng(3)
    p = rng.gamma(0.6, 4.0, len(DAYS)).round(1)
    e = (1.5 + np.sin(2 * np.pi * DAYS.day_of_year / 365.25)).round(1)
    pd.DataFrame({"date": DAYS, "precipitation_mm": p, "makkink_mm": e}).to_csv(folder / "knmi_forcing_249.csv",
                                                                                 index=False)
    radar = pd.DataFrame({"date": DAYS, "precipitation_mm": p * 1.1, "makkink_mm": e,
                          "precipitation_source": np.where(DAYS >= "2020-01-01", "radar_purmer", "knmi_249")})
    radar.loc[DAYS < "2020-01-01", "precipitation_mm"] = p[DAYS < "2020-01-01"]
    radar.to_csv(folder / "radar_forcing_purmer.csv", index=False)
    pd.DataFrame({"date": DAYS, "value": 0.1 * np.sin(np.arange(len(DAYS)) / 30)}).to_csv(
        folder / "stress_lake_edam.csv", index=False)
    pd.DataFrame({"date": DAYS, "value": 1013 + rng.normal(0, 8, len(DAYS))}).to_csv(
        folder / "stress_pressure_240.csv", index=False)


@pytest.fixture
def library(tmp_path):
    _write_forcing(tmp_path)
    return InputLibrary(tmp_path, polders={"purmer_series": "purmer", "beemster_series": "beemster"})


def test_polder_from_the_dike_code():
    assert polder_of("_86349_MB028_(B_BE0213+10_BUKR_GMW)_GMW_PB1_F-451") == "beemster"
    assert polder_of("_87074_MB104_(B_PU0063+0_KRBIB_GMW)_GMW_PB1_F-527") == "purmer"
    assert polder_of("2424-258424_B01_HH0259+67_BUKR_GMW_PB1_F-416") == "heerhugowaard"
    assert polder_of("_88672_MB001_(B_SC0035+55_BUKR_GMW)") is None
    assert polder_of(None) is None


def test_source_labels():
    assert source_label("knmi_249") == "KNMI 249 Berkhout"
    assert source_label("radar_purmer") == "KNMI radar (Purmer)"
    assert source_label("249") == "KNMI 249 Berkhout"
    assert source_label(None) == "–"


def test_extended_types_are_known_and_labelled():
    assert set(EXTENDED_MODEL_TYPES) <= set(MODEL_SPECS)
    assert len(EXTENDED_MODEL_TYPES) == 29 and len(set(EXTENDED_MODEL_TYPES)) == 29
    assert model_spec("linear_gamma_lake_baro_radar").radar
    assert model_spec("flex_exp_lake_baro").stresses == ("lake", "pressure")
    assert model_spec("flex_exp_lake_baro_bucket_radar").stresses == ("lake", "pressure", "bucket")
    assert model_spec("flex_exp_lake_baro_bucket_radar").radar
    assert "radar" in MODEL_LABELS["linear_gamma_lake_baro_radar"]
    assert MODEL_LABELS["linear_gamma_lake_baro_bucket_radar"] == (
        "Lineair + gamma · meer + luchtdruk + bodemvocht · radar")


def test_nested_type_drops_only_the_bucket():
    from tarso_pipeline.fit import nested_type

    assert nested_type("linear_dexp_lake_baro_bucket_radar") == "linear_dexp_lake_baro_radar"
    assert nested_type("flex_gamma_lake_baro_bucket") == "flex_gamma_lake_baro"
    assert nested_type("linear_dexp_lake_baro") is None


def test_bucket_deficit_closes_its_water_balance_and_stays_in_bounds():
    index = pd.date_range("2025-01-01", "2026-12-31", freq="D")
    rng = np.random.default_rng(1)
    p = pd.Series(rng.gamma(0.5, 4.0, len(index)), index)
    e = pd.Series(2.0 + 2.0 * np.sin(2 * np.pi * (index.day_of_year - 80) / 365.25), index)
    deficit, fluxes = inputs.bucket_deficit(p, e, balance=True)
    assert deficit.between(0.0, inputs.BUCKET_SMAX_MM).all()
    # in = out + change in storage (it starts full)
    storage_change = fluxes["storage"].iloc[-1] - inputs.BUCKET_SMAX_MM
    assert p.sum() == pytest.approx(fluxes["actual_et"].sum() + fluxes["overflow"].sum() + storage_change)
    assert (fluxes["actual_et"] <= e + 1e-12).all()
    # dry: below lp * Smax full the plants take less than the potential
    dry = inputs.bucket_deficit(pd.Series(0.0, index[:200]), pd.Series(3.0, index[:200]), balance=True)[1]
    assert dry["actual_et"].iloc[-1] < 3.0 * 0.5


def test_bucket_inputs_are_in_mm_from_the_runs_units(library):
    got = library.inputs("linear_exp_lake_baro_bucket", "purmer_series")
    assert set(got.stresses) == {"lake", "pressure", "bucket"}
    assert got.extra_stresses == "lake_edam,pressure_240,bucket_150_07"
    p, e = library.station_forcing()  # m/day
    expected = inputs.bucket_deficit(p * 1000.0, e * 1000.0)
    pd.testing.assert_series_equal(got.stresses["bucket"], expected)
    radar = library.inputs("linear_exp_lake_baro_bucket_radar", "purmer_series").stresses["bucket"]
    assert radar.max() < expected.max() + 1e-9  # wetter radar: the bucket never empties further


def test_member_forcing_rebuilds_the_bucket_per_member(library):
    member = pd.date_range("1951-01-01", periods=400, freq="D")
    forcing = {"ens01": (pd.Series(0.001, member), pd.Series(0.003, member)),
               "ens02": (pd.Series(0.004, member), pd.Series(0.001, member))}
    assert library.member_forcing("linear_exp_lake_baro", "purmer_series", forcing) is forcing
    built = library.member_forcing("linear_exp_lake_baro_bucket_radar", "purmer_series", forcing)
    dry_p, _, dry = built["ens01"]
    assert set(dry) == {"bucket"}
    assert dry_p.mean() == pytest.approx(0.0011, rel=0.01)  # radar-scaled first
    assert dry["bucket"].iloc[-1] > 100.0 and built["ens02"][2]["bucket"].max() == 0.0


def test_station_and_radar_inputs_with_their_sources(library):
    station = library.inputs("linear_gamma_lake_baro", "purmer_series")
    radar = library.inputs("linear_gamma_lake_baro_radar", "purmer_series")
    assert (station.precipitation_source, station.evaporation_source) == ("knmi_249", "knmi_249")
    assert (radar.precipitation_source, radar.evaporation_source) == ("radar_purmer", "knmi_249")
    assert set(radar.stresses) == {"lake", "pressure"} and radar.extra_stresses == "lake_edam,pressure_240"
    assert library.inputs("tarso", "purmer_series").stresses == {}
    later = radar.precipitation.loc["2021"]
    assert later.sum() == pytest.approx(1.1 * station.precipitation.loc["2021"].sum(), rel=1e-3)


def test_radar_types_only_where_the_polder_has_radar(library):
    assert library.applicable("linear_gamma_lake_baro_radar", "purmer_series")
    assert not library.applicable("linear_gamma_lake_baro_radar", "beemster_series")  # no file here
    assert not library.applicable("linear_gamma_lake_baro_radar", "unknown_series")
    assert library.applicable("linear_gamma_lake_baro", "unknown_series")
    with pytest.raises(ValueError, match="no radar"):
        library.inputs("linear_gamma_lake_baro_radar", "beemster_series")


def test_a_missing_file_falls_back(tmp_path, library):
    empty = tmp_path / "volume"
    empty.mkdir()
    fallback = InputLibrary(empty, fallback=library.directory, polders=library.polders)
    assert fallback.inputs("linear_exp_lake_baro_radar", "purmer_series").precipitation_source == "radar_purmer"


def test_imprex_factors_follow_the_radar_climate(library):
    factors = library.imprex_factors("linear_exp_lake_baro_radar", "purmer_series")
    assert list(factors.index) == list(range(1, 13))
    assert factors.to_numpy() == pytest.approx(1.1, rel=0.01)
    assert library.imprex_factors("linear_exp_lake_baro", "purmer_series") is None
    member = pd.date_range("1951-01-01", periods=60, freq="D")
    forcing = {"ens01": (pd.Series(1.0, member), pd.Series(2.0, member))}
    scaled = scale_members(forcing, factors)
    assert scaled["ens01"][0].mean() == pytest.approx(1.1, rel=0.01)
    assert scaled["ens01"][1].mean() == 2.0
    assert scale_members(forcing, None) is forcing


def test_monthly_factors_on_common_days():
    index = pd.date_range("2020-01-01", "2020-12-31")
    assert monthly_factors(pd.Series(2.0, index), pd.Series(1.0, index)).tolist() == [2.0] * 12


def test_source_columns_in_the_metrics_schema():
    from tarso_pipeline.fit_metrics import FIT_METRICS_SCHEMA

    assert {"precipitation_source", "evaporation_source", "extra_stresses"} <= set(FIT_METRICS_SCHEMA)


@pytest.mark.slow
def test_build_model_adds_the_stresses_as_anomalies(library):
    pytest.importorskip("pastas")
    from tarso_pipeline.fit import build_model

    head = pd.Series(np.sin(np.arange(700) / 50), pd.date_range("2024-01-01", periods=700, freq="D"))
    got = library.inputs("linear_exp_lake_baro_radar", "purmer_series")
    model = build_model("linear_exp_lake_baro_radar", "s", head, got.precipitation, got.evaporation,
                        stresses=got.stresses)
    assert list(model.stressmodels) == ["Recharge", "level", "baro"]  # P/E first: t95 and gain read it
    lake = model.stressmodels["level"].stress[0].series_original
    assert lake.loc[head.index.min():head.index.max()].mean() == pytest.approx(0.0, abs=1e-9)
    assert model.parameters.loc["baro_d", "pmin"] == -10.0
    with pytest.raises(ValueError, match="needs the stress"):
        build_model("linear_exp_lake_baro", "s", head, got.precipitation, got.evaporation)
    flex = build_model("flex_gamma_lake_baro", "s", head, got.precipitation, got.evaporation, stresses=got.stresses)
    assert not flex.parameters.loc["Recharge_kv", "vary"]


@pytest.mark.slow
def test_a_bucket_model_has_a_free_sign_and_is_not_worse_than_without(library):
    pytest.importorskip("pastas")
    from tarso_pipeline.fit import _sse, build_model, solve_model

    got = library.inputs("linear_exp_lake_baro_bucket", "purmer_series")
    index = pd.date_range("2023-01-01", "2026-06-30", freq="D")
    # a head that drops with the bucket deficit beyond the linear response
    head = (-1.0 - 0.004 * got.stresses["bucket"].reindex(index)
            + 0.02 * np.sin(np.arange(len(index)) / 40)).rename("head")
    model = build_model("linear_exp_lake_baro_bucket", "s", head, got.precipitation, got.evaporation,
                        stresses=got.stresses)
    assert list(model.stressmodels) == ["Recharge", "level", "baro", "bucket"]
    assert pd.isna(model.parameters.loc["bucket_A", "pmin"]) and pd.isna(model.parameters.loc["bucket_A", "pmax"])
    solved = solve_model("linear_exp_lake_baro_bucket", "s", head, got.precipitation, got.evaporation,
                         "2023-01-01", "2026-06-30", stresses=got.stresses)
    without = solve_model("linear_exp_lake_baro", "s", head, got.precipitation, got.evaporation,
                          "2023-01-01", "2026-06-30", stresses=got.stresses)
    assert _sse(solved) <= _sse(without) + 1e-9
    assert solved.parameters.loc["bucket_A", "optimal"] < 0  # found the drop's sign

    # On IMPREX the bucket comes from the member's own P and E, not held at its mean.
    from tarso_pipeline.simulate import simulate_member

    member = pd.date_range("1951-01-01", "1958-12-31", freq="D")
    wet, dry = pd.Series(0.004, member), pd.Series(0.0, member)
    forcing = library.member_forcing("linear_exp_lake_baro_bucket", "purmer_series",
                                     {"wet": (wet, pd.Series(0.001, member)), "dry": (dry, pd.Series(0.003, member))})
    heads = {}
    for name, (p, e, synthetic) in forcing.items():
        copy = build_model("linear_exp_lake_baro_bucket", "s", head, got.precipitation, got.evaporation,
                           stresses=got.stresses)
        copy.parameters["optimal"] = solved.parameters["optimal"]
        copy.settings.update(solved.settings)
        heads[name] = simulate_member(copy, p, e, member=name, synthetic=synthetic).head
        assert copy.stressmodels["bucket"].stress[0].series_original.equals(synthetic["bucket"])
    assert heads["dry"].iloc[-1] < heads["wet"].iloc[-1]


@pytest.mark.slow
def test_fit_series_records_the_sources(library, tmp_path):
    pytest.importorskip("pastas")
    from tarso_pipeline.fit import fit_series

    stamps = pd.date_range("2024-01-01", periods=24 * 700, freq="h")
    raw = pd.DataFrame({"tijdstempel": stamps, "waarde": np.sin(np.arange(len(stamps)) / 1200)})
    got = library.inputs("linear_exp_lake_baro_radar", "purmer_series")
    row = fit_series("purmer_series", "s", raw, got.precipitation, got.evaporation, "run",
                     model_type="linear_exp_lake_baro_radar", forcing_station="249", stresses=got.stresses,
                     precipitation_source=got.precipitation_source, evaporation_source=got.evaporation_source)
    assert row["success"], row["error_message"]
    assert (row["precipitation_source"], row["evaporation_source"], row["extra_stresses"]) == (
        "radar_purmer", "knmi_249", "lake,pressure")
    failed = fit_series("x", "s", pd.DataFrame({"x": [1]}), got.precipitation, got.evaporation, "run",
                        forcing_station="249")
    assert not failed["success"] and failed["precipitation_source"] == "knmi_249"


def test_inputs_module_documents_the_files():
    assert "radar_forcing_<polder>.csv" in inputs.__doc__
