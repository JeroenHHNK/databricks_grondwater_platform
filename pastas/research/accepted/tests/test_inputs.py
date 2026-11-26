"""Local forcing, extra stresses and variant builders."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from research.accepted import report, stresses, variants


def test_idw_weights_sum_to_one_and_favour_the_nearest():
    weights = stresses.idw_weights((52.51, 5.04), stresses.RD_STATIONS, n=3)
    assert len(weights) == 3 and sum(weights.values()) == pytest.approx(1.0)
    assert max(weights, key=weights.get) == 224  # Edam


def test_weighted_mean_renormalises_over_missing_stations():
    table = pd.DataFrame({1: [1.0, np.nan], 2: [3.0, 3.0]}, index=pd.date_range("2024-01-01", periods=2))
    out = stresses.weighted(table, {1: 0.5, 2: 0.5})
    assert out.tolist() == [2.0, 3.0]


def test_rd_shift_moves_the_label_one_day_back(monkeypatch):
    table = pd.DataFrame({224: [0.0, 5.0, 0.0]}, index=pd.date_range("2024-01-01", periods=3))
    monkeypatch.setattr(stresses, "_pivot", lambda path, column: table)
    monkeypatch.setattr(stresses, "idw_weights", lambda *a, **k: {224: 1.0})
    shifted = stresses.local_precipitation((52.5, 5.0))
    assert shifted.idxmax() == pd.Timestamp("2024-01-01")  # the 08-08 sum ending on 2 Jan
    assert stresses.local_precipitation((52.5, 5.0), shift_days=0).idxmax() == pd.Timestamp("2024-01-02")


def test_anomaly_has_zero_mean_over_the_calibration_days():
    level = pd.Series(np.arange(10.0), pd.date_range("2024-01-01", periods=10))
    head = level.iloc[2:6]
    assert stresses.anomaly(level, head).loc[head.index].mean() == pytest.approx(0.0)


def test_variant_names_and_tarso_without_extra_stress():
    assert variants.parse("linear_gamma@localP+wl") == ("linear_gamma", ["localP", "wl"])
    assert not variants.applicable("tarso@wl")
    assert variants.applicable("tarso@localP")
    assert "tarso@wl" not in variants.names(["wl"])
    assert report.group_of("flex_gamma@localP") == "flex@localP"
    assert report.group_of("linear_exp") == "baseline"


def test_monthly_factors_scale_the_mean():
    index = pd.date_range("2000-01-01", "2003-12-31", freq="D")
    reference = pd.Series(2.0, index)
    factors = report.monthly_factors(reference * 1.1, reference)
    assert np.allclose(factors.to_numpy(), 1.1)


@pytest.mark.slow
def test_flex_keeps_the_fixed_parameters_fixed():
    ps = pytest.importorskip("pastas")
    index = pd.date_range("2015-01-01", "2020-12-31", freq="D")
    rng = np.random.default_rng(0)
    p = pd.Series(rng.gamma(0.5, 4.0, len(index)) / 1000, index)
    e = pd.Series(1.7 / 1000, index)
    head = pd.Series(rng.normal(0, 0.1, len(index)), index).iloc[1500:]
    model = variants.build("flex_gamma", "s", head, variants.Inputs(p, e))
    fixed = model.parameters.loc[["Recharge_kv", "Recharge_lp", "Recharge_simax"], "vary"]
    assert not fixed.any()
    assert isinstance(model.stressmodels["Recharge"].recharge, ps.rch.FlexModel)


def test_setpoint_deviation_removes_a_step_in_the_set_point():
    index = pd.date_range("2020-01-01", periods=200, freq="D")
    level = pd.Series(np.where(index < "2020-04-01", -1.57, -1.55), index, name="b")
    level.iloc[50] += 0.05
    dev = stresses.setpoint_deviation(level)
    assert abs(dev.iloc[50] - 0.05) < 1e-9
    assert dev.iloc[20:40].abs().max() < 1e-9 and dev.iloc[150:180].abs().max() < 1e-9


@pytest.mark.slow
def test_normalised_boezem_has_unit_std_on_the_calibration_days():
    pytest.importorskip("pastas")
    index = pd.date_range("2019-01-01", "2022-12-31", freq="D")
    rng = np.random.default_rng(1)
    p = pd.Series(rng.gamma(0.5, 4.0, len(index)) / 1000, index)
    e = pd.Series(1.7 / 1000, index)
    head = pd.Series(rng.normal(0, 0.1, len(index)), index).iloc[400:]
    boezem = pd.Series(rng.normal(0, 0.01, len(index)), index)
    model = variants.build("linear_exp@bzn", "s", head, variants.Inputs(p, e, extra={"boezem": boezem}))
    stress = model.stressmodels["boezem"].stress[0].series_original
    assert abs(stress.loc[head.index.min():].std() - 1.0) < 1e-6


# --- pumping stations --------------------------------------------------------------

def _quarter_hours(values, start="2024-01-01"):
    return pd.Series(values, pd.date_range(start, periods=len(values), freq="15min"), dtype=float)


def test_clean_gemaal_flags_band_spike_and_flat_and_averages_duplicates():
    rng = np.random.default_rng(1)
    values = -0.50 + 0.005 * rng.standard_normal(2000)
    values[100] = -4.3          # the polder level in the boezem column: band
    values[500] = -0.35         # 15 cm above its neighbours: spike
    values[1000:1300] = -0.512  # 300 identical quarter-hours (> 2 days): flat
    series = _quarter_hours(values)
    series = pd.concat([series, series.iloc[[10]]])  # a duplicate stamp (the DST fall-back hour)
    kept, removed = stresses.clean_gemaal(series)
    reasons = removed["reason"]
    assert reasons[series.index[100]] == "band"
    assert reasons[series.index[500]] == "spike"
    assert (reasons[series.index[1000:1300]] == "flat").all()
    assert kept.index.is_unique and len(kept) + len(removed) == 2000


def test_clean_gemaal_keeps_a_normal_record_whole():
    series = _quarter_hours(-5.0 + 0.02 * np.sin(np.arange(3000) / 200))
    kept, removed = stresses.clean_gemaal(series)
    assert removed.empty and len(kept) == 3000


def test_positions_parse_dike_and_cross_section_level(monkeypatch, tmp_path):
    frame = pd.DataFrame({"meetreeks_id": ["a", "b", "c", "d"],
                          "meetlocatie_code": ["_86349_MB028_(B_BE0213+10_BUKR_GMW)_GMW_PB1_F-451",
                                               "_87074_MB104_(B_PU0063+0_KRBIB_GMW)_GMW_PB1_F-527",
                                               "2424-258424_B02_HH0259+68_BIT_GMW_PB1_F-468", None],
                          "lat": [52.5, 52.5, 52.7, np.nan], "lon": [4.9, 5.0, 4.8, np.nan]})
    path = tmp_path / "object_data.csv"
    frame.to_csv(path, index=False)
    monkeypatch.setattr(stresses, "OBJECT_DATA", path)
    where = stresses.positions()
    assert where.loc["a", ["polder", "pos", "level"]].tolist() == ["Beemster", "BUKR", "upper"]
    assert where.loc["b", ["polder", "pos", "level"]].tolist() == ["Purmer", "KRBIB", "lower"]
    assert where.loc["c", ["polder", "pos", "level"]].tolist() == ["Heerhugowaard", "BIT", "lower"]
    assert pd.isna(where.loc["d", "polder"])


def test_stations_nearest_first_and_purmer_has_one():
    east = stresses.stations_by_distance(52.56, 4.97, "Beemster")   # next to Jacobus Bouman
    west = stresses.stations_by_distance(52.57, 4.87, "Beemster")   # next to Wouter Sluis
    assert east[0][0] == "jacobus_bouman" and west[0][0] == "wouter_sluis"
    assert [s for s, _ in stresses.stations_by_distance(52.49, 5.0, "Purmer")] == ["purmer_stedelijk"]


def test_pos_gives_crest_the_uitstroompeil_and_toe_the_instroompeil(monkeypatch):
    from research.accepted import run_variants

    up = pd.Series(1.0, pd.date_range("2024-01-01", periods=3), name="up")
    ip = pd.Series(2.0, pd.date_range("2024-01-01", periods=3), name="ip")
    where = pd.DataFrame({"polder": ["Purmer", "Purmer"], "level": ["upper", "lower"],
                          "lat": [52.49, 52.49], "lon": [5.0, 5.0]}, index=["crest", "toe"])
    shared = {"positions": where, "gemalen": {("purmer_stedelijk", "uitstroompeil"): up,
                                              ("purmer_stedelijk", "instroompeil"): ip}}
    crest, toe = (run_variants.gemaal_stresses(i, shared) for i in ("crest", "toe"))
    assert crest["gemaal_pos"] is up and toe["gemaal_pos"] is ip
    assert "gemaal_pos_far" not in crest  # one station in the Purmer


def test_radar_grid_index_maps_the_product_corners_to_the_image_edges():
    pytest.importorskip("pyproj")
    from research.accepted import radar

    geographic = {"proj4": "+proj=stere +lat_0=90 +lon_0=0.0 +lat_ts=60.0 +a=6378.137 +b=6356.752 +x_0=0 +y_0=0",
                  "size_x": 1.0, "size_y": -1.0, "column_offset": 0.0, "row_offset": 3650.0}
    assert radar.grid_index(55.974 - 1e-3, 0.0 + 1e-3, geographic) == (0, 0)        # upper left
    row, column = radar.grid_index(48.895 + 1e-3, 9.009 - 1e-3, geographic)         # lower right
    assert (row, column) == (764, 699)


# --- drought indicators (research/accepted/drought.py) ---------------------------------
from research.accepted import drought  # noqa: E402


def _dry_year():
    index = pd.date_range("2025-01-01", "2026-12-31", freq="D")
    precipitation = pd.Series(np.where(index.month.isin([6, 7]), 0.0, 2.0), index=index)
    evaporation = pd.Series(np.where(index.month.isin([4, 5, 6, 7, 8]), 4.0, 0.5), index=index)
    return precipitation, evaporation


def test_deficit_resets_on_1_april_and_is_zero_from_october():
    precipitation, evaporation = _dry_year()
    d = drought.deficit(precipitation, evaporation)
    assert (d >= 0).all()
    assert d.loc["2025-03-31"] == 0 and d.loc["2025-04-01"] == pytest.approx(2.0)
    assert d.loc["2025-10-01":"2026-03-31"].eq(0).all()
    assert d.idxmax() == pd.Timestamp("2025-08-31")  # dry until August ends, then wetter
    above = drought.deficit(precipitation, evaporation, threshold=100.0)
    assert above.max() == pytest.approx(d.max() - 100.0)


def test_rolling_is_dry_side_only():
    precipitation, evaporation = _dry_year()
    r = drought.rolling(precipitation, evaporation, days=30)
    assert (r >= 0).all() and r.loc["2025-12-15"] == 0
    assert r.loc["2025-07-31"] == pytest.approx(30 * 4.0)


def test_bucket_closes_its_water_balance_and_stays_in_bounds():
    precipitation, evaporation = _dry_year()
    out, fluxes = drought.bucket(precipitation, evaporation, smax=100.0, lp=0.5, balance=True)
    assert out.between(0, 100).all()
    change = fluxes["storage"].iloc[-1] - 100.0
    assert change == pytest.approx(precipitation.sum() - fluxes["actual_et"].sum() - fluxes["overflow"].sum())
    assert out.loc["2025-07-31"] > 50  # dry by the end of July


def test_parts_parse_and_a_drought_variant_adds_a_free_sign_stress():
    assert drought.is_part("def50") and drought.is_part("roll60") and drought.is_part("bkt100l5")
    assert not drought.is_part("wl1") and not variants.applicable("tarso@def50")
    precipitation, evaporation = _dry_year()
    head = pd.Series(np.sin(np.arange(400) / 50.0), index=pd.date_range("2025-06-01", periods=400))
    inputs = variants.Inputs(precipitation / 1000.0, evaporation / 1000.0)
    model = variants.build("linear_exp@def0", "s", head, inputs)
    assert "drought" in model.stressmodels
    assert np.isnan(model.parameters.loc["drought_A", "pmin"])  # up=None: either sign
    stress = model.stressmodels["drought"].stress[0].series_original
    pd.testing.assert_series_equal(stress, drought.deficit(precipitation, evaporation), check_names=False)
