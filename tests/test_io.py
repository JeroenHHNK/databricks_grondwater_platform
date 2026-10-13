import pandas as pd
import pytest

from tarso_pipeline.io import HeadFormat, load_forcing, read_head_csv


def write(tmp_path, name, text, encoding="utf-8"):
    path = tmp_path / name
    path.write_bytes(text.encode(encoding))
    return path


RAW = """Time,head
2023-02-02 12:00:00,-0.389
2023-02-02 13:00:00,-0.387
2023-02-02 14:00:00,-0.380
"""

VALIDATED = """timestamp,raw_data,gecontroleerd,afgekeurd,note
2025-01-14 14:00:00,-2.1305,1,1,
2025-01-14 15:00:00,-1.6180,1,0,
2025-01-14 16:00:00,-1.6000,0,0,
2025-01-14 17:00:00,-1.5900,1,0,ok
"""


def test_raw_default_column(tmp_path):
    res = read_head_csv(write(tmp_path, "a.csv", RAW))
    assert res.format is HeadFormat.RAW
    assert len(res.series) == 3
    assert res.n_rejected == 0
    assert isinstance(res.series.index, pd.DatetimeIndex)


def test_raw_timestamp_and_other_value_name_uses_first_numeric(tmp_path):
    text = "Timestamp,Waterniveau\n2023-01-01 00:00:00,1.5\n2023-01-01 01:00:00,1.6\n"
    res = read_head_csv(write(tmp_path, "a.csv", text), head_column="head")
    assert res.format is HeadFormat.RAW
    assert res.series.tolist() == [1.5, 1.6]


def test_raw_ignores_leading_text_column(tmp_path):
    text = "Time,label,Waterniveau\n2023-01-01 00:00:00,x,1.5\n2023-01-01 01:00:00,y,1.6\n"
    res = read_head_csv(write(tmp_path, "a.csv", text))
    assert res.series.tolist() == [1.5, 1.6]


def test_validated_filter_drops_rejected_and_unchecked(tmp_path):
    res = read_head_csv(write(tmp_path, "v.csv", VALIDATED))
    assert res.format is HeadFormat.VALIDATED
    # row1 rejected, row3 not checked -> 2 rows kept
    assert res.series.tolist() == [-1.6180, -1.5900]
    assert res.n_rows_read == 4
    assert res.n_rejected == 2


def test_validated_rows_are_dropped_not_nan_filled(tmp_path):
    res = read_head_csv(write(tmp_path, "v.csv", VALIDATED))
    assert not res.series.isna().any()
    assert len(res.series) == 2  # no reindexing to close the gap


def test_validated_with_utf8_bom(tmp_path):
    res = read_head_csv(write(tmp_path, "v.csv", VALIDATED, encoding="utf-8-sig"))
    assert res.format is HeadFormat.VALIDATED
    assert len(res.series) == 2


def test_validated_columns_case_and_whitespace_insensitive(tmp_path):
    text = (
        "Timestamp, Raw_Data ,Gecontroleerd,AFGEKEURD\n"
        "2025-01-14 15:00:00,-1.6,1,0\n"
        "2025-01-14 16:00:00,-1.7,1,1\n"
    )
    res = read_head_csv(write(tmp_path, "v.csv", text, encoding="utf-8-sig"))
    assert res.format is HeadFormat.VALIDATED
    assert res.series.tolist() == [-1.6]


def test_partial_verdict_columns_fall_back_to_raw(tmp_path):
    text = "timestamp,raw_data,gecontroleerd\n2025-01-14 15:00:00,-1.6,1\n2025-01-14 16:00:00,-1.7,0\n"
    res = read_head_csv(write(tmp_path, "p.csv", text))
    assert res.format is HeadFormat.RAW
    assert len(res.series) == 2  # raw is never filtered


def test_validation_filter_can_be_disabled(tmp_path):
    res = read_head_csv(write(tmp_path, "v.csv", VALIDATED), apply_validation_filter=False)
    assert len(res.series) == 4
    assert res.n_rejected == 0


def test_unsorted_input_is_sorted(tmp_path):
    text = "Time,head\n2023-01-02 00:00:00,2\n2023-01-01 00:00:00,1\n"
    res = read_head_csv(write(tmp_path, "u.csv", text))
    assert res.series.index.is_monotonic_increasing
    assert res.series.tolist() == [1.0, 2.0]


def test_empty_file_raises(tmp_path):
    with pytest.raises(ValueError):
        read_head_csv(write(tmp_path, "e.csv", "Time,head\n"))


def test_load_forcing_by_parameterised_column(tmp_path):
    text = "date,precipitation_mm\n1951-01-01,0.121\n1951-01-02,0.0\n"
    s = load_forcing(write(tmp_path, "p.csv", text), "precipitation_mm")
    assert s.index[0] == pd.Timestamp("1951-01-01")
    assert s.tolist() == [0.121, 0.0]


def test_load_forcing_missing_column_lists_available(tmp_path):
    text = "date,makkink_mm\n1951-01-01,0.2\n"
    with pytest.raises(KeyError, match="makkink_mm"):
        load_forcing(write(tmp_path, "e.csv", text), "evaporation")
