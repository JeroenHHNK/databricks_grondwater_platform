import re

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


# --- manifest and duplicate check ------------------------------------------


def test_manifest_is_capped():
    from tarso_pipeline.meting import manifest_sql

    assert "LIMIT 20" in manifest_sql(TABLE, limit=20)
    assert "LIMIT 5" in manifest_sql(TABLE, limit=5)


def test_manifest_orders_longest_first():
    from tarso_pipeline.meting import manifest_sql

    assert "ORDER BY span_days DESC, n_values DESC" in manifest_sql(TABLE)


def test_manifest_takes_only_trainable_series():
    from tarso_pipeline.meting import TRAINABLE, manifest_sql

    assert f"WHERE verdict = '{TRAINABLE}'" in manifest_sql(TABLE)


def test_manifest_rejects_a_silly_limit():
    from tarso_pipeline.meting import manifest_sql

    with pytest.raises(ValueError, match="limit"):
        manifest_sql(TABLE, limit=0)


def test_duplicate_check_separates_duplicates_from_dense_sampling():
    # n_duplicate_ts tells them apart; distinct_ts_per_day shows real density.
    from tarso_pipeline.meting import duplicate_timestamps_sql

    sql = duplicate_timestamps_sql(TABLE, "w.manifest")
    assert "n_duplicate_ts" in sql
    assert "distinct_ts_per_day" in sql
    assert "rows_per_day" in sql


def test_duplicate_check_ignores_rows_without_a_value():
    from tarso_pipeline.meting import duplicate_timestamps_sql

    assert f"m.{VALUE_COLUMN} IS NOT NULL" in duplicate_timestamps_sql(TABLE, "mani")


def test_series_rows_filters_to_one_series_and_drops_nulls():
    from tarso_pipeline.meting import series_rows_sql

    sql = series_rows_sql(TABLE, "a" * 64)
    assert f"= '{'a' * 64}'" in sql
    assert f"{VALUE_COLUMN} IS NOT NULL" in sql


@pytest.mark.parametrize(
    "bad",
    ["x' OR '1'='1", "a; DROP TABLE t", "", "a" * 200, "has space"],
)
def test_series_rows_rejects_anything_that_is_not_a_hash(bad):
    from tarso_pipeline.meting import series_rows_sql

    with pytest.raises(ValueError, match="plain hash"):
        series_rows_sql(TABLE, bad)


# --- flatness --------------------------------------------------------------
#
# Databricks raises AnalysisException on a WITH nested inside a CTE body, so no
# statement this module builds may contain more than one WITH.


def _all_statements():
    from tarso_pipeline import meting as m

    return {
        "overview": m.overview_sql(TABLE),
        "per_series": m.per_series_sql(TABLE),
        "span_summary": m.span_summary_sql(TABLE),
        "span_buckets": m.span_buckets_sql(TABLE),
        "series_verdict": m.series_verdict_sql(TABLE),
        "verdict_summary": m.verdict_summary_sql(TABLE),
        "trainable": m.trainable_series_sql(TABLE),
        "manifest": m.manifest_sql(TABLE),
        "status": m.status_sql(TABLE),
        "duplicates": m.duplicate_timestamps_sql(TABLE, "mani"),
        "series_rows": m.series_rows_sql(TABLE, "a" * 64),
    }


@pytest.mark.parametrize("name", list(_all_statements()))
def test_no_statement_nests_a_cte(name):
    import re

    sql = _all_statements()[name]
    assert len(re.findall(r"\bWITH\b", sql, re.IGNORECASE)) <= 1, (
        f"{name} nests a CTE; Databricks rejects WITH inside a CTE body"
    )


def test_composed_statements_use_sibling_ctes():
    from tarso_pipeline.meting import manifest_sql

    sql = manifest_sql(TABLE)
    assert "WITH per_series AS (" in sql
    assert "scored AS (" in sql
    assert sql.index("per_series AS (") < sql.index("scored AS (")


# --- column preflight ------------------------------------------------------


def test_check_columns_passes_on_the_expected_shape():
    from tarso_pipeline.meting import REQUIRED_COLUMNS, check_columns

    check_columns(["id", *REQUIRED_COLUMNS, "validatie_status", "tijdstempel_utc"])


def test_check_columns_names_what_is_missing():
    from tarso_pipeline.meting import check_columns

    with pytest.raises(ValueError, match="waarde"):
        check_columns(["id", "meetreeks_id", "tijdstempel"])


def test_check_columns_shows_what_the_table_actually_has():
    from tarso_pipeline.meting import check_columns

    with pytest.raises(ValueError, match="value_column"):
        check_columns(["series", "value_column", "ts"])


# --- source columns keep their Unity Catalog names -------------------------
#
# A second name for a column that already had one is the defect this guards
# against: you query `meetreeks_id` upstream, and the same value has to still be
# called `meetreeks_id` in the manifest, the metrics table and the CSVs, or the
# key cannot be traced across the pipeline by reading it.

SOURCE_COLUMNS = ("meetreeks_id", "id", "waarde", "tijdstempel", "validatie_status")


def _every_statement():
    """Every SQL builder in the module, with arguments that exercise it."""
    from tarso_pipeline import meting as m

    table = "cat.sch.meting"
    return {
        "overview_sql": m.overview_sql(table),
        "per_series_sql": m.per_series_sql(table),
        "span_summary_sql": m.span_summary_sql(table),
        "span_buckets_sql": m.span_buckets_sql(table),
        "series_verdict_sql": m.series_verdict_sql(table),
        "verdict_summary_sql": m.verdict_summary_sql(table),
        "trainable_series_sql": m.trainable_series_sql(table),
        "manifest_sql": m.manifest_sql(table),
        "status_sql": m.status_sql(table),
        "duplicate_timestamps_sql": m.duplicate_timestamps_sql(table, "cat.sch.manifest"),
        "series_rows_sql": m.series_rows_sql(table, "a" * 64),
    }


@pytest.mark.parametrize("column", SOURCE_COLUMNS)
def test_no_statement_aliases_a_source_column_to_another_name(column):
    # Only a bare column reference can be a rename. `count(waarde) AS n_values`
    # does not match, because the character before the whitespace is ")" -- that
    # names a derived value, which is exactly what an alias is for.
    pattern = re.compile(rf"\b(?:\w+\.)?{column}\s+AS\s+(\w+)", re.IGNORECASE)
    for name, sql in _every_statement().items():
        for alias in pattern.findall(sql):
            assert alias.lower() == column, (
                f"{name} renames the source column {column!r} to {alias!r}. "
                "Source columns keep their Unity Catalog names; alias only values "
                "the source does not have."
            )


def test_the_key_is_called_meetreeks_id_everywhere_it_is_selected():
    statements = _every_statement()
    for name in ("per_series_sql", "trainable_series_sql", "manifest_sql",
                 "series_verdict_sql", "duplicate_timestamps_sql"):
        assert "meetreeks_id" in statements[name]
    # The old pipeline-local name must not come back.
    for name, sql in statements.items():
        assert "series_id" not in sql, f"{name} still uses the renamed key"


def test_the_manifest_join_matches_on_the_source_name():
    sql = _every_statement()["duplicate_timestamps_sql"]
    assert "w.meetreeks_id = m.meetreeks_id" in sql
