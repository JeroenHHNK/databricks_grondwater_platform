import pytest

from tarso_pipeline.discover import DISCOVERY_COLUMNS, discover_series, is_series_file


def build_tree(root, layout):
    """layout: {survey: [filenames]}"""
    for survey, names in layout.items():
        folder = root / survey
        folder.mkdir(parents=True, exist_ok=True)
        for name in names:
            (folder / name).write_text("Time,head\n2023-01-01 00:00:00,-1.0\n")
    return root


def test_one_row_per_series(tmp_path):
    root = build_tree(tmp_path / "input", {"83034-2": ["a.csv", "b.csv"], "87074-1": ["c.csv"]})
    found = discover_series(root)
    assert list(found.columns) == list(DISCOVERY_COLUMNS)
    assert len(found) == 3
    assert set(found["survey"]) == {"83034-2", "87074-1"}


def test_object_data_is_excluded(tmp_path):
    root = build_tree(tmp_path / "input", {"s": ["a.csv", "OBJECT_DATA.csv"]})
    found = discover_series(root)
    assert found["series_name"].tolist() == ["a"]


def test_object_data_exclusion_is_case_insensitive(tmp_path):
    root = build_tree(tmp_path / "input", {"s": ["a.csv", "object_data.csv"]})
    assert len(discover_series(root)) == 1


def test_non_csv_files_are_ignored(tmp_path):
    root = build_tree(tmp_path / "input", {"s": ["a.csv"]})
    (root / "s" / "notes.txt").write_text("hello")
    (root / "s" / "model.pas").write_text("{}")
    assert discover_series(root)["series_name"].tolist() == ["a"]


def test_series_name_is_the_sanitised_stem(tmp_path):
    # Spec 2.3: source files are never renamed; the model name is derived.
    messy = "83034-2 HB017PB01 HH0114+34_BUKR_GMW_PB1_F-264.csv"
    root = build_tree(tmp_path / "input", {"83034-2": [messy]})
    name = discover_series(root)["series_name"].iloc[0]
    assert name == "83034-2_HB017PB01_HH0114_34_BUKR_GMW_PB1_F-264"
    assert " " not in name and "+" not in name


def test_surveys_argument_limits_the_scan(tmp_path):
    root = build_tree(tmp_path / "input", {"keep": ["a.csv"], "drop": ["b.csv"]})
    found = discover_series(root, surveys=["keep"])
    assert found["survey"].tolist() == ["keep"]


def test_unknown_survey_raises_rather_than_silently_returning_nothing(tmp_path):
    root = build_tree(tmp_path / "input", {"keep": ["a.csv"]})
    with pytest.raises(NotADirectoryError, match="not found"):
        discover_series(root, surveys=["typo"])


def test_missing_root_raises(tmp_path):
    with pytest.raises(NotADirectoryError):
        discover_series(tmp_path / "nope")


def test_empty_tree_returns_an_empty_frame_with_columns(tmp_path):
    root = tmp_path / "input"
    root.mkdir()
    found = discover_series(root)
    assert found.empty
    assert list(found.columns) == list(DISCOVERY_COLUMNS)


def test_loose_files_at_the_root_are_not_series(tmp_path):
    # Series live in survey folders; a stray CSV at the root is not one.
    root = build_tree(tmp_path / "input", {"s": ["a.csv"]})
    (root / "stray.csv").write_text("x\n")
    assert len(discover_series(root)) == 1


def test_is_series_file(tmp_path):
    assert is_series_file(tmp_path / "a.CSV")
    assert not is_series_file(tmp_path / "OBJECT_DATA.csv")
    assert not is_series_file(tmp_path / "a.txt")
