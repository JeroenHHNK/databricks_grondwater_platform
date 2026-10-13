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
