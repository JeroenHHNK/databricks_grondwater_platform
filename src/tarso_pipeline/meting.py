"""SQL over the `meting` head-measurement table.

The head series live in a Delta table rather than one CSV per well, so discovery
is a ``GROUP BY meetreeks_id`` instead of a directory listing. This module builds
the SQL; the notebooks run it and display the result.

Column meanings, from the source table:

``id``                row identifier (one measurement)
``meetreeks_id``      **series** identifier -- "meetreeks" is a measurement series,
                      so this, not ``id``, is what becomes a Pastas model
``waarde``            the measured value (head)
``validatie_status``  the reviewer's verdict
``tijdstempel``       observation timestamp (``tijdstempel_utc`` is the UTC twin)

**Every statement here is flat.** Databricks rejects a ``WITH`` nested inside a
CTE body with ``AnalysisException``, so the shared pieces are assembled as
sibling CTEs (``WITH a AS (...), b AS (...) SELECT ...``) rather than by
embedding one finished statement inside another.

**Source columns are never renamed.** ``meetreeks_id``, ``waarde``,
``tijdstempel`` and ``validatie_status`` keep the names Unity Catalog gives them,
all the way through to the output tables and the CSV artefacts. An alias here is
only ever for a value the source does not have -- a count, a span, a verdict --
so any name the pipeline invents is visibly a derived quantity rather than a
second name for something that already had one. ``test_meting.py`` enforces this.
"""

from __future__ import annotations

import re

SERIES_COLUMN = "meetreeks_id"
ROW_ID_COLUMN = "id"
VALUE_COLUMN = "waarde"
TIME_COLUMN = "tijdstempel"
STATUS_COLUMN = "validatie_status"

# The columns the pipeline depends on; checked against the real table before a run.
REQUIRED_COLUMNS = (SERIES_COLUMN, VALUE_COLUMN, TIME_COLUMN)

# Only plain identifiers: these are interpolated into SQL, and the table name
# arrives from a notebook widget.
_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

TRAINABLE = "trainable"

# Why a series cannot be modelled. Ordered: the first matching reason wins, so a
# series with no values at all is reported as such rather than as "too short".
VERDICTS = (TRAINABLE, "no values", "too few values", "too short")


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


def check_columns(found: list[str]) -> None:
    """Fail early and clearly when the table is not shaped as assumed.

    An AnalysisException from deep inside a generated query is far harder to read
    than a list of the columns we wanted and the ones that are actually there.
    """
    missing = [c for c in REQUIRED_COLUMNS if c not in found]
    if missing:
        raise ValueError(
            f"The source table is missing {missing}. Expected {list(REQUIRED_COLUMNS)}; "
            f"the table has {sorted(found)}. If the names differ, update the column "
            "constants in tarso_pipeline/meting.py."
        )


# --- CTE fragments ----------------------------------------------------------
#
# Each returns "<name> AS (...)" so callers can compose them as siblings.


def _per_series_cte(
    table: str,
    values_only: bool = False,
    valued_extent: bool = False,
) -> str:
    """One row per series: counts, extent and span.

    Two independent choices, because the verdict path and the comparison path
    need different things:

    ``values_only``    filter the rows with ``WHERE waarde IS NOT NULL``. Drops
                       value-less series from the result entirely, so the verdict
                       path must NOT use it -- "no values" is a verdict to report,
                       not a series to lose.
    ``valued_extent``  measure the extent between the first and last row that has
                       a ``waarde``, rather than between bare timestamps. This is
                       what the verdict path wants: a series carrying two years of
                       empty rows is not two years of data.
    """
    where = f"WHERE {VALUE_COLUMN} IS NOT NULL" if values_only else ""
    moment = (
        f"CASE WHEN {VALUE_COLUMN} IS NOT NULL THEN {TIME_COLUMN} END"
        if valued_extent
        else TIME_COLUMN
    )
    return f"""per_series AS (
            SELECT
                {SERIES_COLUMN},
                count(*)                                    AS n_rows,
                count({VALUE_COLUMN})                       AS n_values,
                min({moment})                               AS first_value_at,
                max({moment})                               AS last_value_at,
                datediff(max({moment}), min({moment}))      AS span_days
            FROM {table}
            {where}
            GROUP BY {SERIES_COLUMN}
        )"""


def _scored_cte(min_days: int, min_obs: int) -> str:
    """Adds the verdict to ``per_series``. Verdicts are ordered and exclusive."""
    return f"""scored AS (
            SELECT
                meetreeks_id, n_rows, n_values, first_value_at, last_value_at, span_days,
                CASE
                    WHEN n_values = 0          THEN 'no values'
                    WHEN n_values < {min_obs}  THEN 'too few values'
                    WHEN span_days < {min_days} THEN 'too short'
                    ELSE '{TRAINABLE}'
                END                                         AS verdict
            FROM per_series
        )"""


def _check_thresholds(min_days: int, min_obs: int = 0) -> None:
    if min_days < 0:
        raise ValueError(f"min_days must be >= 0, got {min_days}")
    if min_obs < 0:
        raise ValueError(f"min_obs must be >= 0, got {min_obs}")


def _scored_prefix(table: str, min_days: int, min_obs: int) -> str:
    """The flat ``WITH per_series AS (...), scored AS (...)`` preamble."""
    _check_thresholds(min_days, min_obs)
    # No row filter, so value-less series survive to be reported as "no values";
    # valued extent, so their span is measured over measurements, not timestamps.
    per_series = _per_series_cte(table, values_only=False, valued_extent=True)
    return f"WITH {per_series},\n        {_scored_cte(min_days, min_obs)}"


# --- statements -------------------------------------------------------------


def overview_sql(table: str) -> str:
    """One row: how much data there is, and how much of it has values."""
    return f"""
        SELECT
            count(*)                                              AS rows_total,
            count(DISTINCT {ROW_ID_COLUMN})                       AS unique_row_ids,
            count(DISTINCT {SERIES_COLUMN})                       AS unique_series,
            count({VALUE_COLUMN})                                 AS rows_with_value,
            count(*) - count({VALUE_COLUMN})                      AS rows_without_value,
            round(100.0 * count({VALUE_COLUMN}) / count(*), 2)    AS pct_with_value,
            min({TIME_COLUMN})                                    AS first_timestamp,
            max({TIME_COLUMN})                                    AS last_timestamp
        FROM {table}
    """


def per_series_sql(table: str, values_only: bool = False) -> str:
    """One row per series, as a standalone SELECT (no CTE).

    Safe to embed in a caller's own ``WITH x AS (...)``, which is how the
    handover's selection snippet uses it.
    """
    where = f"WHERE {VALUE_COLUMN} IS NOT NULL" if values_only else ""
    valued_time = f"CASE WHEN {VALUE_COLUMN} IS NOT NULL THEN {TIME_COLUMN} END"
    return f"""
        SELECT
            {SERIES_COLUMN},
            count(*)                                        AS n_rows,
            count({VALUE_COLUMN})                           AS n_values,
            min({valued_time})                              AS first_value_at,
            max({valued_time})                              AS last_value_at,
            datediff(max({valued_time}), min({valued_time})) AS span_days
        FROM {table}
        {where}
        GROUP BY {SERIES_COLUMN}
    """


def span_summary_sql(table: str, min_days: int = 365, values_only: bool = False) -> str:
    """How many series clear ``min_days``, and how many do not."""
    _check_thresholds(min_days)
    return f"""
        WITH {_per_series_cte(table, values_only)}
        SELECT
            count(*)                                                   AS series_total,
            sum(CASE WHEN span_days >= {min_days} THEN 1 ELSE 0 END)   AS series_long_enough,
            sum(CASE WHEN span_days <  {min_days} THEN 1 ELSE 0 END)   AS series_too_short,
            round(100.0 * sum(CASE WHEN span_days >= {min_days} THEN 1 ELSE 0 END)
                  / count(*), 2)                                       AS pct_long_enough,
            round(avg(span_days), 1)                                   AS mean_span_days,
            percentile_approx(span_days, 0.5)                          AS median_span_days,
            max(span_days)                                             AS max_span_days
        FROM per_series
    """


def span_buckets_sql(table: str, values_only: bool = False) -> str:
    """Series counted into span bands, to show the shape rather than one number."""
    return f"""
        WITH {_per_series_cte(table, values_only)}
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


def series_verdict_sql(table: str, min_days: int = 365, min_obs: int = 100) -> str:
    """One row per series, with a verdict on whether it can be modelled."""
    return f"""
        {_scored_prefix(table, min_days, min_obs)}
        SELECT * FROM scored
    """


def verdict_summary_sql(table: str, min_days: int = 365, min_obs: int = 100) -> str:
    """How many series fall into each verdict -- the headline count."""
    return f"""
        {_scored_prefix(table, min_days, min_obs)}
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
        {_scored_prefix(table, min_days, min_obs)}
        SELECT meetreeks_id, n_values, first_value_at, last_value_at, span_days
        FROM scored
        WHERE verdict = '{TRAINABLE}'
        ORDER BY span_days DESC, n_values DESC
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
        {_scored_prefix(table, min_days, min_obs)}
        SELECT meetreeks_id, n_rows, n_values, first_value_at, last_value_at, span_days
        FROM scored
        WHERE verdict = '{TRAINABLE}'
        ORDER BY span_days DESC, n_values DESC
        LIMIT {limit}
    """


def status_sql(table: str) -> str:
    """Distribution of the reviewer verdict, and how it relates to missing values."""
    return f"""
        SELECT
            {STATUS_COLUMN},
            count(*)                                          AS rows,
            count({VALUE_COLUMN})                             AS rows_with_value,
            count(DISTINCT {SERIES_COLUMN})                   AS series
        FROM {table}
        GROUP BY {STATUS_COLUMN}
        ORDER BY rows DESC
    """


def duplicate_timestamps_sql(table: str, manifest_table: str) -> str:
    """How many timestamps repeat, per manifest series (handover section 4).

    ~55 values a day is either duplicates or sub-hourly sampling, and the two
    need different cleaning rules. ``distinct_ts_per_day`` separates them:
    duplicates push ``n_duplicate_ts`` up, genuine dense sampling leaves it at
    zero.
    """
    return f"""
        SELECT
            m.{SERIES_COLUMN},
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
        JOIN {manifest_table} w ON w.meetreeks_id = m.{SERIES_COLUMN}
        WHERE m.{VALUE_COLUMN} IS NOT NULL
        GROUP BY m.{SERIES_COLUMN}
        ORDER BY n_duplicate_ts DESC
    """


def series_rows_sql(table: str, meetreeks_id: str) -> str:
    """Every valued row of one series, for loading into pandas.

    ``meetreeks_id`` is a 64-character hash from the manifest; it is validated
    rather than trusted, because it is interpolated into SQL.
    """
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", meetreeks_id or ""):
        raise ValueError(f"meetreeks_id must be a plain hash/identifier, got {meetreeks_id!r}")
    return f"""
        SELECT {TIME_COLUMN}, {VALUE_COLUMN}
        FROM {table}
        WHERE {SERIES_COLUMN} = '{meetreeks_id}'
          AND {VALUE_COLUMN} IS NOT NULL
        ORDER BY {TIME_COLUMN}
    """
