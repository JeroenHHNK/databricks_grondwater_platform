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
