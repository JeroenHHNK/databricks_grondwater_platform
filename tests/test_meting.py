import pytest

from tarso_pipeline.meting import (
    SERIES_COLUMN,
    VALUE_COLUMN,
    overview_sql,
    per_series_sql,
    qualify,
    span_buckets_sql,
    span_summary_sql,
    status_sql,
)

TABLE = "cat.sch.meting"


def test_qualify_builds_a_three_part_name():
    assert qualify("hl_grondwaterpeilmeting", "default", "meting") == (
        "hl_grondwaterpeilmeting.default.meting"
    )


@pytest.mark.parametrize(
    "bad",
    [
        "meting; DROP TABLE x",
        "meting WHERE 1=1",
        "cat.sch",
        "",
        "2meting",
        "me-ting",
        "`meting`",
    ],
)
def test_qualify_rejects_anything_that_is_not_an_identifier(bad):
    # These names are interpolated into SQL and arrive from a widget.
    with pytest.raises(ValueError, match="plain SQL identifier"):
        qualify("cat", "sch", bad)


def test_qualify_names_the_offending_part():
    with pytest.raises(ValueError, match="schema"):
        qualify("cat", "bad schema", "meting")


# --- the series/row distinction --------------------------------------------


def test_overview_counts_rows_and_series_separately():
    sql = overview_sql(TABLE)
    assert "count(DISTINCT id)" in sql
    assert f"count(DISTINCT {SERIES_COLUMN})" in sql


def test_per_series_groups_by_the_series_column_not_the_row_id():
    # meetreeks_id is the series; id is one measurement. Grouping by id would
    # give one "series" per row and a span of zero everywhere.
    assert f"GROUP BY {SERIES_COLUMN}" in per_series_sql(TABLE)


# --- the values-only distinction -------------------------------------------


def test_values_only_filters_out_null_measurements():
    assert f"WHERE {VALUE_COLUMN} IS NOT NULL" in per_series_sql(TABLE, values_only=True)


def test_all_rows_basis_has_no_where_clause():
    assert "WHERE" not in per_series_sql(TABLE, values_only=False)


def test_span_summary_carries_the_basis_through():
    assert "IS NOT NULL" in span_summary_sql(TABLE, values_only=True)
    assert "IS NOT NULL" not in span_summary_sql(TABLE, values_only=False)


def test_buckets_carry_the_basis_through():
    assert "IS NOT NULL" in span_buckets_sql(TABLE, values_only=True)


# --- the threshold ---------------------------------------------------------


def test_min_days_threshold_appears_on_both_sides():
    sql = span_summary_sql(TABLE, min_days=365)
    assert "span_days >= 365" in sql
    assert "span_days <  365" in sql


def test_a_different_threshold_is_honoured():
    assert "span_days >= 730" in span_summary_sql(TABLE, min_days=730)
    assert "365" not in span_summary_sql(TABLE, min_days=730)


def test_negative_min_days_is_rejected():
    with pytest.raises(ValueError, match="min_days"):
        span_summary_sql(TABLE, min_days=-1)


def test_every_builder_targets_the_given_table():
    for sql in (
        overview_sql(TABLE),
        per_series_sql(TABLE),
        span_summary_sql(TABLE),
        span_buckets_sql(TABLE),
        status_sql(TABLE),
    ):
        assert TABLE in sql
