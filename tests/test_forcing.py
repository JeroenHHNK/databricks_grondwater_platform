import pandas as pd
import pytest

from tarso_pipeline.forcing import (
    UNIT_SCALES,
    check_coverage,
    forcing_file,
    forcing_filename,
    load_forcing_pair,
)

CSV = """station,date,precipitation_mm,makkink_mm
249,2020-01-01,10.0,2.0
249,2020-01-02,0.0,1.0
249,2020-01-03,5.5,3.5
"""


def write(tmp_path, text=CSV, name="knmi_forcing_249.csv"):
    path = tmp_path / name
    path.write_text(text)
    return str(path)


# --- the station is a parameter, the filename is derived -------------------


def test_filename_is_derived_from_the_station():
    assert forcing_filename(249) == "knmi_forcing_249.csv"
    assert forcing_filename(235) == "knmi_forcing_235.csv"
    assert forcing_filename("235") == "knmi_forcing_235.csv"


def test_forcing_file_joins_directory_and_derived_name():
    assert forcing_file("/Volumes/a/b", 249) == "/Volumes/a/b/knmi_forcing_249.csv"


def test_trailing_slash_does_not_double_up():
    assert forcing_file("/Volumes/a/b/", 249) == "/Volumes/a/b/knmi_forcing_249.csv"


# --- units -----------------------------------------------------------------


def test_metres_is_millimetres_over_a_thousand(tmp_path):
    prec, evap = load_forcing_pair(write(tmp_path), units="m")
    assert prec.tolist() == [0.01, 0.0, 0.0055]
    assert evap.tolist() == [0.002, 0.001, 0.0035]


def test_millimetres_pass_through_unchanged(tmp_path):
    prec, evap = load_forcing_pair(write(tmp_path), units="mm")
    assert prec.tolist() == [10.0, 0.0, 5.5]
    assert evap.tolist() == [2.0, 1.0, 3.5]


def test_the_two_unit_systems_differ_by_exactly_one_thousand(tmp_path):
    path = write(tmp_path)
    in_m, _ = load_forcing_pair(path, units="m")
    in_mm, _ = load_forcing_pair(path, units="mm")
    assert (in_mm / 1000).tolist() == in_m.tolist()
    assert UNIT_SCALES["m"] == 0.001


def test_unknown_units_are_rejected(tmp_path):
    with pytest.raises(ValueError, match="Unknown units"):
        load_forcing_pair(write(tmp_path), units="cm")


# --- the loaded series -----------------------------------------------------


def test_index_is_a_naive_datetimeindex(tmp_path):
    prec, _ = load_forcing_pair(write(tmp_path))
    assert isinstance(prec.index, pd.DatetimeIndex)
    assert prec.index.tz is None
    assert str(prec.index[0].date()) == "2020-01-01"


def test_rows_are_sorted_and_deduplicated(tmp_path):
    text = CSV + "249,2020-01-01,99.0,99.0\n"
    prec, _ = load_forcing_pair(write(tmp_path, text), units="mm")
    assert len(prec) == 3
    assert prec.index.is_monotonic_increasing
    assert prec.iloc[0] == 10.0  # first kept, not the duplicate


def test_missing_columns_are_named(tmp_path):
    bad = "station,date,rain\n249,2020-01-01,1.0\n"
    with pytest.raises(ValueError, match="makkink_mm"):
        load_forcing_pair(write(tmp_path, bad))


# --- coverage against the warmup -------------------------------------------


def series(start, end):
    index = pd.date_range(start, end, freq="D")
    return pd.Series(1.0, index=index)


def test_ample_forcing_reports_warmup_ok():
    cov = check_coverage(series("2000-01-01", "2026-01-01"),
                         series("2015-01-01", "2020-01-01"), warmup_days=3650)
    assert cov.warmup_ok
    assert cov.usable_warmup_days == 3650
    assert "warmup OK" in cov.describe()


def test_short_forcing_shortens_the_warmup_and_says_so():
    cov = check_coverage(series("2010-01-01", "2026-01-01"),
                         series("2012-01-01", "2020-01-01"), warmup_days=3650)
    assert not cov.warmup_ok
    assert cov.available_warmup_days == 730  # 2010 + 2011, neither a leap year
    assert cov.usable_warmup_days == 730
    assert "WARMUP SHORTENED" in cov.describe()


def test_forcing_starting_after_the_head_does_not_cover_it():
    cov = check_coverage(series("2015-01-01", "2026-01-01"),
                         series("2010-01-01", "2020-01-01"))
    assert not cov.covers_calibration
    assert "DOES NOT COVER" in cov.describe()


def test_forcing_ending_before_the_head_does_not_cover_it():
    cov = check_coverage(series("2000-01-01", "2018-01-01"),
                         series("2010-01-01", "2020-01-01"))
    assert not cov.covers_calibration


def test_usable_warmup_is_never_negative():
    # Forcing starting after the head gives a negative gap; asking Pastas for a
    # negative warmup would be nonsense.
    cov = check_coverage(series("2015-01-01", "2026-01-01"),
                         series("2010-01-01", "2020-01-01"), warmup_days=3650)
    assert cov.usable_warmup_days == 0


def test_station_249_cannot_warm_up_a_series_starting_before_2009():
    # Real constraint: 249 Berkhout starts 1999-03-23.
    cov = check_coverage(series("1999-03-23", "2026-10-12"),
                         series("2005-01-01", "2010-01-01"), warmup_days=3650)
    assert not cov.warmup_ok
    cov_ok = check_coverage(series("1999-03-23", "2026-10-12"),
                            series("2010-01-01", "2015-01-01"), warmup_days=3650)
    assert cov_ok.warmup_ok
