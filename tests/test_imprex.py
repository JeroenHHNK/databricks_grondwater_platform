import pandas as pd
import pytest

from tarso_pipeline.imprex import discover_members, load_member

HEADER_P = "date,precipitation_mm\n"
HEADER_E = "date,makkink_mm\n"
ROWS = "1951-01-01,10.0\n1951-01-02,0.0\n1951-01-03,5.5\n"


def build(tmp_path, members=("ens01", "ens02"), location="berkhout", skip_evap=()):
    for member in members:
        (tmp_path / f"imprex_{location}_{member}_daily_precipitation.csv").write_text(
            HEADER_P + ROWS
        )
        if member not in skip_evap:
            (tmp_path / f"imprex_{location}_{member}_daily_evaporation.csv").write_text(
                HEADER_E + ROWS
            )
    return tmp_path


def test_members_are_discovered_by_globbing(tmp_path):
    found = discover_members(str(build(tmp_path)))
    assert [m.member for m in found] == ["ens01", "ens02"]


def test_a_member_without_its_evaporation_partner_is_excluded(tmp_path):
    # Better one fewer member in the summary than a mid-run file-not-found.
    found = discover_members(str(build(tmp_path, skip_evap=("ens02",))))
    assert [m.member for m in found] == ["ens01"]


def test_members_are_not_assumed_to_be_one_to_sixteen(tmp_path):
    found = discover_members(str(build(tmp_path, members=("ens03", "ens07"))))
    assert [m.member for m in found] == ["ens03", "ens07"]


def test_another_location_is_not_picked_up(tmp_path):
    build(tmp_path, members=("ens01",), location="berkhout")
    build(tmp_path, members=("ens01",), location="elsewhere")
    assert len(discover_members(str(tmp_path), "berkhout")) == 1


def test_an_empty_directory_yields_no_members(tmp_path):
    assert discover_members(str(tmp_path)) == []


def test_units_are_applied_to_both_series(tmp_path):
    member = discover_members(str(build(tmp_path, members=("ens01",))))[0]
    in_mm_p, in_mm_e = load_member(member, units="mm")
    in_m_p, in_m_e = load_member(member, units="m")
    assert in_mm_p.tolist() == [10.0, 0.0, 5.5]
    assert in_m_p.tolist() == [0.01, 0.0, 0.0055]
    assert (in_mm_e / 1000).tolist() == in_m_e.tolist()


def test_the_index_is_naive_and_midnight_aligned(tmp_path):
    member = discover_members(str(build(tmp_path, members=("ens01",))))[0]
    prec, _ = load_member(member)
    assert isinstance(prec.index, pd.DatetimeIndex)
    assert prec.index.tz is None
    assert (prec.index == prec.index.normalize()).all()


def test_a_missing_value_column_is_named(tmp_path):
    path = tmp_path / "imprex_berkhout_ens01_daily_precipitation.csv"
    path.write_text("date,rain\n1951-01-01,1.0\n")
    (tmp_path / "imprex_berkhout_ens01_daily_evaporation.csv").write_text(HEADER_E + ROWS)
    member = discover_members(str(tmp_path))[0]
    with pytest.raises(ValueError, match="precipitation_mm"):
        load_member(member)


def test_unknown_units_are_rejected(tmp_path):
    member = discover_members(str(build(tmp_path, members=("ens01",))))[0]
    with pytest.raises(ValueError, match="Unknown units"):
        load_member(member, units="inches")
