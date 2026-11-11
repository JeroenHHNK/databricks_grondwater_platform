import pytest

from tarso_pipeline.io import load_forcing
from tarso_pipeline.knmi import parse_daily_csv, write_export
from tarso_pipeline.paths import forcing_dir, forcing_path, repo_root

SAMPLE = """# STN,YYYYMMDD,   RH, EV24
  249,20200101,    0,    1
  249,20200102,   71,   12
"""


def test_repo_root_finds_this_repo():
    root = repo_root()
    assert (root / "pyproject.toml").is_file()
    assert (root / "src" / "tarso_pipeline").is_dir()


def test_repo_root_walks_up_from_a_subdirectory(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n")
    nested = tmp_path / "a" / "b"
    nested.mkdir(parents=True)
    assert repo_root(nested) == tmp_path.resolve()


def test_repo_root_raises_outside_a_repo(tmp_path):
    # An explicit start is searched and nothing else, so a directory with no
    # pyproject above it must fail loudly rather than silently returning the
    # repo this code happens to live in.
    with pytest.raises(RuntimeError, match="Cannot locate the repo root"):
        repo_root(tmp_path)


def test_forcing_path_is_inside_the_forcing_dir(tmp_path):
    assert forcing_path(249, tmp_path).parent == forcing_dir(tmp_path)
    assert forcing_path(249, tmp_path).name == "knmi_forcing_249.csv"


def test_station_gets_its_own_file(tmp_path):
    assert forcing_path(249, tmp_path) != forcing_path(235, tmp_path)


def test_committed_forcing_file_is_readable_by_load_forcing(tmp_path):
    # The contract the next notebook depends on: one file, two columns, read
    # through the existing loader with no KNMI-specific code.
    path = write_export(parse_daily_csv(SAMPLE), forcing_path(249, tmp_path))

    precipitation = load_forcing(path, "precipitation_mm")
    evaporation = load_forcing(path, "makkink_mm")

    assert precipitation.tolist() == [0.0, 7.1]
    assert evaporation.tolist() == [0.1, 1.2]
    assert precipitation.index.tolist() == evaporation.index.tolist()
    assert str(precipitation.index[0].date()) == "2020-01-01"


# --- run folder layout -----------------------------------------------------

import datetime as dt

from tarso_pipeline.paths import (
    ARTEFACT_DIRS,
    DIAGNOSTICS_DIR,
    PAS_DIR,
    RESULTS_DIR,
    SERIES_ID_CHARS,
    artefact_dir,
    run_dir,
    run_folder_name,
    series_name_from_id,
)

HASH = "a1b2c3d4e5f60718293a4b5c6d7e8f90" + "0" * 32  # a 64-char meetreeks_id


def test_series_name_keeps_24_hex_by_default():
    assert SERIES_ID_CHARS == 24
    name = series_name_from_id(HASH)
    assert name == "series_a1b2c3d4e5f60718293a4b5c"
    assert len(name) == len("series_") + 24


def test_series_name_length_is_configurable():
    assert series_name_from_id(HASH, chars=16) == "series_a1b2c3d4e5f60718"


def test_24_chars_distinguishes_ids_that_16_would_not():
    a = "f" * 16 + "aaaa" + "1" * 44
    b = "f" * 16 + "bbbb" + "1" * 44
    assert series_name_from_id(a, chars=16) == series_name_from_id(b, chars=16)
    assert series_name_from_id(a) != series_name_from_id(b)


def test_empty_meetreeks_id_is_rejected():
    with pytest.raises(ValueError, match="empty"):
        series_name_from_id("")


def test_run_folder_is_dated():
    assert run_folder_name(dt.date(2026, 10, 15)) == "run_26_10_15"
    assert run_folder_name(dt.date(2027, 1, 3)) == "run_27_01_03"


def test_run_dir_sits_under_the_volume_base():
    assert run_dir("/Volumes/a/b", dt.date(2026, 10, 15)) == "/Volumes/a/b/run_26_10_15"
    assert run_dir("/Volumes/a/b/", dt.date(2026, 10, 15)) == "/Volumes/a/b/run_26_10_15"


def test_an_explicit_name_overrides_the_date():
    # Two runs in one day would otherwise share a folder and overwrite artefacts.
    assert run_dir("/Volumes/a", name="run_26_10_15_b") == "/Volumes/a/run_26_10_15_b"


def test_the_three_artefact_folders():
    base = run_dir("/Volumes/a", dt.date(2026, 10, 15))
    assert artefact_dir(base, PAS_DIR).endswith("/run_26_10_15/pas_files")
    assert artefact_dir(base, RESULTS_DIR).endswith("/run_26_10_15/results_files")
    assert artefact_dir(base, DIAGNOSTICS_DIR).endswith("/run_26_10_15/diagnostics_files")
    assert set(ARTEFACT_DIRS) == {PAS_DIR, RESULTS_DIR, DIAGNOSTICS_DIR}


def test_an_unknown_artefact_kind_is_rejected():
    with pytest.raises(ValueError, match="Unknown artefact kind"):
        artefact_dir("/Volumes/a/run_26_10_15", "random_files")


def test_both_data_bases_write_the_same_tables_under_their_own_prefix():
    from tarso_pipeline import paths

    for kind in paths.TABLE_KINDS:
        valid = paths.output_table(paths.VALIDATED, kind)
        unvalid = paths.output_table(paths.UNVALIDATED, kind)
        assert valid == f"waterkeringbeheer_prd.jvanruitenbeek.valid_pastas_{kind}"
        assert unvalid == f"waterkeringbeheer_prd.jvanruitenbeek.unvalid_pastas_{kind}"
    assert paths.source_table(paths.UNVALIDATED) == paths.SOURCE_TABLE
    assert paths.source_table(paths.VALIDATED).endswith(".validated_meting")
    assert paths.basis_volume("/Volumes/x/", paths.VALIDATED) == "/Volumes/x/valid_pastas"
    with pytest.raises(ValueError):
        paths.output_table("raw", "fit_metrics")
    with pytest.raises(ValueError):
        paths.output_table(paths.VALIDATED, "nonsense")
