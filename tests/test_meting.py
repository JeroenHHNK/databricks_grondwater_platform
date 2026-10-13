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


# --- verdicts --------------------------------------------------------------


def test_every_verdict_is_reachable_in_the_sql():
    from tarso_pipeline.meting import VERDICTS, series_verdict_sql

    sql = series_verdict_sql(TABLE)
    for verdict in VERDICTS:
        assert f"'{verdict}'" in sql


def test_verdict_order_puts_no_values_before_too_short():
    # A series with no values must be reported as such, not as "too short":
    # ordering the CASE the other way would hide the real problem.
    from tarso_pipeline.meting import series_verdict_sql

    sql = series_verdict_sql(TABLE)
    assert sql.index("'no values'") < sql.index("'too short'")


def test_span_is_measured_between_rows_that_have_values():
    # Not between bare timestamps: empty rows are not data.
    from tarso_pipeline.meting import series_verdict_sql

    sql = series_verdict_sql(TABLE)
    assert f"CASE WHEN {VALUE_COLUMN} IS NOT NULL" in sql
    assert "first_value_at" in sql and "last_value_at" in sql


def test_both_thresholds_are_applied():
    from tarso_pipeline.meting import series_verdict_sql

    sql = series_verdict_sql(TABLE, min_days=400, min_obs=50)
    assert "n_values < 50" in sql
    assert "< 400" in sql


def test_trainable_list_selects_only_the_trainable_verdict():
    from tarso_pipeline.meting import TRAINABLE, trainable_series_sql

    assert f"WHERE verdict = '{TRAINABLE}'" in trainable_series_sql(TABLE)


def test_verdict_builders_reject_negative_thresholds():
    from tarso_pipeline.meting import series_verdict_sql

    with pytest.raises(ValueError, match="min_obs"):
        series_verdict_sql(TABLE, min_obs=-1)
    with pytest.raises(ValueError, match="min_days"):
        series_verdict_sql(TABLE, min_days=-1)
