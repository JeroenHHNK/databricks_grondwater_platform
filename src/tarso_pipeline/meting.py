"""Profiling the `meting` head-measurement table.

The head series live in a Delta table rather than one CSV per well, so discovery
is a `GROUP BY meetreeks_id` instead of a directory listing. This module builds
the SQL; the notebook runs it and displays the result.

Column meanings, from the source table:

``id``                row identifier (one measurement)
``meetreeks_id``      **series** identifier -- "meetreeks" is a measurement series,
                      so this, not ``id``, is what becomes a Pastas model
``waarde``            the measured value (head)
``validatie_status``  the reviewer's verdict
``tijdstempel``       observation timestamp (``tijdstempel_utc`` is the UTC twin)

Two spans are reported per series: over **all** rows, and over rows with a
non-null ``waarde``. They differ whenever a series carries timestamps without
measurements, and only the second one can actually be fitted.
"""

from __future__ import annotations

import re

SERIES_COLUMN = "meetreeks_id"
ROW_ID_COLUMN = "id"
VALUE_COLUMN = "waarde"
TIME_COLUMN = "tijdstempel"
STATUS_COLUMN = "validatie_status"

# Only plain identifiers: these are interpolated into SQL, and the table name
# arrives from a notebook widget.
_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def qualify(catalog: str, schema: str, table: str) -> str:
    """``catalog.schema.table``, rejecting anything that is not an identifier."""
    parts = {"catalog": catalog, "schema": schema, "table": table}
    for name, value in parts.items():
        if not _IDENTIFIER.match(value or ""):
            raise ValueError(
                f"{name} must be a plain SQL identifier (letters, digits, underscore), "
                f"got {value!r}"
            )
    return f"{catalog}.{schema}.{table}"


def overview_sql(table: str) -> str:
    """One row: how much data there is, and how much of it has values."""
    return f"""
        SELECT
            count(*)                                              AS rows_total,
            count(DISTINCT {ROW_ID_COLUMN})                       AS unique_row_ids,
            count(DISTINCT {SERIES_COLUMN})                       AS unique_series,
            count({VALUE_COLUMN})                                 AS rows_with_value,
            count(*) - count({VALUE_COLUMN})                      AS rows_without_value,
            round(100.0 * count({VALUE_COLUMN}) / count(*), 2)     AS pct_with_value,
            min({TIME_COLUMN})                                    AS first_timestamp,
            max({TIME_COLUMN})                                    AS last_timestamp
        FROM {table}
    """


def per_series_sql(table: str, values_only: bool = False) -> str:
    """One row per series: extent, counts and span in days.

    ``values_only`` restricts to rows with a non-null ``waarde`` -- the span that
    matters for fitting, as opposed to the span of bare timestamps.
    """
    where = f"WHERE {VALUE_COLUMN} IS NOT NULL" if values_only else ""
    return f"""
        SELECT
            {SERIES_COLUMN}                                            AS series_id,
            count(*)                                                   AS n_rows,
            count({VALUE_COLUMN})                                      AS n_values,
            min({TIME_COLUMN})                                         AS first_timestamp,
            max({TIME_COLUMN})                                         AS last_timestamp,
            datediff(max({TIME_COLUMN}), min({TIME_COLUMN}))           AS span_days
        FROM {table}
        {where}
        GROUP BY {SERIES_COLUMN}
    """


def span_summary_sql(table: str, min_days: int = 365, values_only: bool = False) -> str:
    """How many series clear ``min_days``, and how many do not."""
    if min_days < 0:
        raise ValueError(f"min_days must be >= 0, got {min_days}")
    return f"""
        WITH per_series AS ({per_series_sql(table, values_only)})
        SELECT
            count(*)                                                       AS series_total,
            sum(CASE WHEN span_days >= {min_days} THEN 1 ELSE 0 END)       AS series_long_enough,
            sum(CASE WHEN span_days <  {min_days} THEN 1 ELSE 0 END)       AS series_too_short,
            round(100.0 * sum(CASE WHEN span_days >= {min_days} THEN 1 ELSE 0 END)
                  / count(*), 2)                                           AS pct_long_enough,
            round(avg(span_days), 1)                                       AS mean_span_days,
            percentile_approx(span_days, 0.5)                              AS median_span_days,
            max(span_days)                                                 AS max_span_days
        FROM per_series
    """


def span_buckets_sql(table: str, values_only: bool = False) -> str:
    """Series counted into span bands, to show the shape rather than one number."""
    return f"""
        WITH per_series AS ({per_series_sql(table, values_only)})
        SELECT
            CASE
                WHEN span_days <   30 THEN 'a. under 1 month'
                WHEN span_days <  182 THEN 'b. 1-6 months'
                WHEN span_days <  365 THEN 'c. 6-12 months'
                WHEN span_days <  730 THEN 'd. 1-2 years'
                WHEN span_days < 1826 THEN 'e. 2-5 years'
                ELSE                       'f. 5 years or more'
            END                                   AS span_band,
            count(*)                              AS series,
            sum(n_values)                         AS values_total
        FROM per_series
        GROUP BY 1
        ORDER BY 1
    """


TRAINABLE = "trainable"

# Why a series cannot be modelled. Ordered: the first matching reason wins, so a
# series with no values at all is reported as such rather than as "too short".
VERDICTS = (TRAINABLE, "no values", "too few values", "too short")


def series_verdict_sql(table: str, min_days: int = 365, min_obs: int = 100) -> str:
    """One row per series, with a verdict on whether it can be modelled.

    The span is measured **between the first and last row that actually has a
    ``waarde``**, not between bare timestamps: a series carrying two years of
    empty rows is not two years of data.

    Verdicts are mutually exclusive and ordered, so the counts add up to the
    total and each series is reported by its most fundamental problem.
    """
    if min_days < 0:
        raise ValueError(f"min_days must be >= 0, got {min_days}")
    if min_obs < 0:
        raise ValueError(f"min_obs must be >= 0, got {min_obs}")
    return f"""
        WITH per_series AS (
            SELECT
                {SERIES_COLUMN}                                       AS series_id,
                count(*)                                              AS n_rows,
                count({VALUE_COLUMN})                                 AS n_values,
                min(CASE WHEN {VALUE_COLUMN} IS NOT NULL
                         THEN {TIME_COLUMN} END)                      AS first_value_at,
                max(CASE WHEN {VALUE_COLUMN} IS NOT NULL
                         THEN {TIME_COLUMN} END)                      AS last_value_at
            FROM {table}
            GROUP BY {SERIES_COLUMN}
        )
        SELECT
            series_id,
            n_rows,
            n_values,
            first_value_at,
            last_value_at,
            datediff(last_value_at, first_value_at)                   AS span_days,
            CASE
                WHEN n_values = 0          THEN 'no values'
                WHEN n_values < {min_obs}  THEN 'too few values'
                WHEN datediff(last_value_at, first_value_at) < {min_days}
                                           THEN 'too short'
                ELSE '{TRAINABLE}'
            END                                                       AS verdict
        FROM per_series
    """


def verdict_summary_sql(table: str, min_days: int = 365, min_obs: int = 100) -> str:
    """How many series fall into each verdict -- the headline count."""
    return f"""
        WITH scored AS ({series_verdict_sql(table, min_days, min_obs)})
        SELECT
            verdict,
            count(*)                                      AS series,
            sum(n_values)                                 AS values_total,
            round(avg(span_days), 1)                      AS mean_span_days,
            max(span_days)                                AS max_span_days
        FROM scored
        GROUP BY verdict
        ORDER BY series DESC
    """


def trainable_series_sql(table: str, min_days: int = 365, min_obs: int = 100) -> str:
    """Just the series that can be modelled, longest first."""
    return f"""
        WITH scored AS ({series_verdict_sql(table, min_days, min_obs)})
        SELECT series_id, n_values, first_value_at, last_value_at, span_days
        FROM scored
        WHERE verdict = '{TRAINABLE}'
        ORDER BY span_days DESC, n_values DESC
    """


def status_sql(table: str) -> str:
    """Distribution of the reviewer verdict, and how it relates to missing values."""
    return f"""
        SELECT
            {STATUS_COLUMN}                                   AS validatie_status,
            count(*)                                          AS rows,
            count({VALUE_COLUMN})                             AS rows_with_value,
            count(DISTINCT {SERIES_COLUMN})                   AS series
        FROM {table}
        GROUP BY {STATUS_COLUMN}
        ORDER BY rows DESC
    """


def manifest_sql(
    table: str,
    min_days: int = 365,
    min_obs: int = 100,
    limit: int = 20,
) -> str:
    """The work list: the longest trainable series, longest first.

    Deliberately capped (handover section 3): the first increment is 20 series,
    not the whole table.
    """
    if limit < 1:
        raise ValueError(f"limit must be >= 1, got {limit}")
    return f"""
        WITH scored AS ({series_verdict_sql(table, min_days, min_obs)})
        SELECT series_id, n_rows, n_values, first_value_at, last_value_at, span_days
        FROM scored
        WHERE verdict = '{TRAINABLE}'
        ORDER BY span_days DESC, n_values DESC
        LIMIT {limit}
    """


def duplicate_timestamps_sql(table: str, manifest_table: str) -> str:
    """How many timestamps repeat, per manifest series (handover section 4).

    ~55 values a day is either duplicates or sub-hourly sampling, and the two
    need different cleaning rules. ``values_per_day`` separates them: duplicates
    push ``n_duplicate_ts`` up, genuine dense sampling leaves it at zero.
    """
    return f"""
        SELECT
            m.{SERIES_COLUMN}                                          AS series_id,
            count(*)                                                   AS n_rows,
            count(DISTINCT m.{TIME_COLUMN})                            AS n_distinct_ts,
            count(*) - count(DISTINCT m.{TIME_COLUMN})                 AS n_duplicate_ts,
            round(100.0 * (count(*) - count(DISTINCT m.{TIME_COLUMN}))
                  / count(*), 2)                                       AS pct_duplicate,
            round(count(*) / nullif(
                datediff(max(m.{TIME_COLUMN}), min(m.{TIME_COLUMN})), 0), 1)
                                                                       AS rows_per_day,
            round(count(DISTINCT m.{TIME_COLUMN}) / nullif(
                datediff(max(m.{TIME_COLUMN}), min(m.{TIME_COLUMN})), 0), 1)
                                                                       AS distinct_ts_per_day
        FROM {table} m
        JOIN {manifest_table} w ON w.series_id = m.{SERIES_COLUMN}
        WHERE m.{VALUE_COLUMN} IS NOT NULL
        GROUP BY m.{SERIES_COLUMN}
        ORDER BY n_duplicate_ts DESC
    """


def series_rows_sql(table: str, series_id: str) -> str:
    """Every valued row of one series, for loading into pandas.

    ``series_id`` is a 64-character hash from the manifest; it is validated
    rather than trusted, because it is interpolated into SQL.
    """
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", series_id or ""):
        raise ValueError(f"series_id must be a plain hash/identifier, got {series_id!r}")
    return f"""
        SELECT {TIME_COLUMN} AS tijdstempel, {VALUE_COLUMN} AS waarde
        FROM {table}
        WHERE {SERIES_COLUMN} = '{series_id}'
          AND {VALUE_COLUMN} IS NOT NULL
        ORDER BY {TIME_COLUMN}
    """
