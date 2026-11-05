import pandas as pd
import pytest

from tarso_pipeline.clean import clean_head


def frame(timestamps, values, tz=None):
    index = pd.to_datetime(list(timestamps))
    if tz:
        index = index.tz_localize(tz)
    return pd.DataFrame({"tijdstempel": index, "waarde": list(values)})


def test_duplicate_timestamps_are_collapsed():
    # The handover's core problem: ~55 values per day on one instant.
    df = frame(["2021-01-01"] * 4 + ["2021-01-02"], [1.0, 2.0, 3.0, 4.0, 9.0])
    out = clean_head(df)
    assert out.n_duplicate_timestamps == 3
    assert out.n_after_dedup == 2
    assert out.series.iloc[0] == 2.5  # mean of 1,2,3,4


def test_dedup_method_is_honoured():
    df = frame(["2021-01-01"] * 3, [1.0, 2.0, 30.0])
    assert clean_head(df, dedup_method="median").series.iloc[0] == 2.0
    assert clean_head(df, dedup_method="max").series.iloc[0] == 30.0


def test_counts_tell_the_whole_story():
    df = frame(
        ["2021-01-01", "2021-01-01", "2021-01-02", "2021-01-03"],
        [1.0, 3.0, None, 5.0],
    )
    out = clean_head(df)
    assert out.n_raw == 4
    assert out.n_missing_value == 1
    assert out.n_duplicate_timestamps == 1
    assert out.n_after_dedup == 2
    assert out.n_final == 2


def test_timezone_aware_input_becomes_naive():
    # meting carries an offset; the forcing index is naive. Comparing the two
    # raises, so the conversion has to happen here.
    df = frame(["2021-01-01 12:00", "2021-01-02 12:00"], [1.0, 2.0], tz="UTC")
    out = clean_head(df)
    assert out.series.index.tz is None


def test_sub_hourly_sampling_resamples_without_dedup():
    # Dense but distinct timestamps: nothing to deduplicate, the resample does
    # the work. This is the other explanation for ~55 values a day.
    index = pd.date_range("2021-01-01", periods=48, freq="30min")
    out = clean_head(frame(index, range(48)))
    assert out.n_duplicate_timestamps == 0
    assert out.n_final == 1  # a single day


def test_resample_rule_and_method_are_honoured():
    index = pd.date_range("2021-01-01", periods=48, freq="h")
    out = clean_head(frame(index, range(48)), resample_rule="D", resample_method="max")
    assert out.n_final == 2
    assert out.series.iloc[0] == 23


def test_span_days_is_measured_on_the_cleaned_series():
    df = frame(["2021-01-01", "2022-01-01"], [1.0, 2.0])
    assert clean_head(df).span_days == 365


def test_empty_input_does_not_raise():
    out = clean_head(frame([], []))
    assert out.n_final == 0
    assert out.span_days == 0


def test_all_values_missing_yields_an_empty_series():
    out = clean_head(frame(["2021-01-01", "2021-01-02"], [None, None]))
    assert out.n_final == 0
    assert out.n_missing_value == 2


def test_unparseable_timestamps_are_dropped_not_guessed():
    df = pd.DataFrame({"tijdstempel": ["2021-01-01", "nonsense"], "waarde": [1.0, 2.0]})
    assert clean_head(df).n_final == 1


def test_wrong_columns_raise_with_a_readable_message():
    with pytest.raises(KeyError, match="Expected columns"):
        clean_head(pd.DataFrame({"x": [1]}))


def test_unsupported_method_is_rejected():
    df = frame(["2021-01-01"], [1.0])
    with pytest.raises(ValueError, match="dedup_method"):
        clean_head(df, dedup_method="eval")
    with pytest.raises(ValueError, match="resample_method"):
        clean_head(df, resample_method="__import__")


def test_as_row_carries_the_cleaning_rule():
    row = clean_head(frame(["2021-01-01"], [1.0])).as_row()
    assert row["dedup_method"] == "mean"
    assert row["resample_rule"] == "D"
    assert set(row) == {
        "n_raw_rows", "n_missing_value", "n_duplicate_timestamps", "n_after_dedup",
        "dedup_method", "resample_rule", "resample_method",
    }
