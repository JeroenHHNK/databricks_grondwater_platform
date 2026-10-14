"""Turning raw `meting` rows into a series Pastas will accept (handover section 4).

The three longest series carry roughly 59,000 values over about 1,069 days -- some
55 per day, far denser than hourly. That is either duplicate timestamps or
sub-hourly sampling, and Pastas wants a regular, deduplicated series: feeding it
duplicates produces a failure or, worse, a silently meaningless fit.

So the cleaning rule is **explicit and recorded**, never implicit:

1. drop rows with no value,
2. collapse duplicate timestamps with ``dedup_method``,
3. resample to ``rule`` with ``resample_method``, then ``dropna()``.

Every count along the way is returned, so the metrics row can state what was
actually done and two fits stay comparable.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

# Aggregations allowed when collapsing duplicates or resampling. An allowlist
# keeps a job parameter from turning into arbitrary attribute access.
ALLOWED_METHODS = ("mean", "median", "min", "max", "first", "last")


@dataclass(frozen=True)
class CleanedSeries:
    """A Pastas-ready series plus the audit trail of how it got that way."""

    series: pd.Series
    n_raw: int  # rows in
    n_missing_value: int  # dropped for a null value
    n_duplicate_timestamps: int  # rows removed by the dedup step
    n_after_dedup: int
    n_final: int  # after resampling
    dedup_method: str
    resample_rule: str
    resample_method: str

    @property
    def span_days(self) -> int:
        if len(self.series) < 2:
            return 0
        return int((self.series.index.max() - self.series.index.min()).days)

    def as_row(self) -> dict:
        """The cleaning fields, for the metrics table."""
        return {
            "n_raw_rows": self.n_raw,
            "n_missing_value": self.n_missing_value,
            "n_duplicate_timestamps": self.n_duplicate_timestamps,
            "n_after_dedup": self.n_after_dedup,
            "dedup_method": self.dedup_method,
            "resample_rule": self.resample_rule,
            "resample_method": self.resample_method,
        }


def clean_head(
    frame: pd.DataFrame,
    time_column: str = "tijdstempel",
    value_column: str = "waarde",
    dedup_method: str = "mean",
    resample_rule: str = "D",
    resample_method: str = "mean",
) -> CleanedSeries:
    """Clean one series' raw rows into a Pastas-ready daily series.

    ``frame`` is the raw rows for a single series. Timestamps are made
    timezone-naive: the source carries an offset, Pastas compares against a
    naive forcing index, and mixing the two raises rather than silently
    misaligning.
    """
    for name, method in (("dedup_method", dedup_method), ("resample_method", resample_method)):
        if method not in ALLOWED_METHODS:
            raise ValueError(f"Unsupported {name} {method!r}; use one of {ALLOWED_METHODS}")
    if time_column not in frame.columns or value_column not in frame.columns:
        raise KeyError(
            f"Expected columns {time_column!r} and {value_column!r}; "
            f"got {list(frame.columns)}"
        )

    n_raw = len(frame)

    values = pd.to_numeric(frame[value_column], errors="coerce")
    index = pd.to_datetime(frame[time_column], errors="coerce", utc=True)
    series = pd.Series(values.to_numpy(dtype=float), index=pd.DatetimeIndex(index))

    # An offset-aware index cannot be compared with the naive forcing index.
    if series.index.tz is not None:
        series.index = series.index.tz_convert(None)

    series = series[series.index.notna()]
    series = series.dropna()
    n_missing_value = n_raw - len(series)

    series = series.sort_index()
    before_dedup = len(series)
    if series.index.has_duplicates:
        series = getattr(series.groupby(level=0), dedup_method)()
    n_duplicate_timestamps = before_dedup - len(series)
    n_after_dedup = len(series)

    if n_after_dedup:
        series = getattr(series.resample(resample_rule), resample_method)().dropna()
    series.name = "head"

    return CleanedSeries(
        series=series,
        n_raw=n_raw,
        n_missing_value=n_missing_value,
        n_duplicate_timestamps=n_duplicate_timestamps,
        n_after_dedup=n_after_dedup,
        n_final=len(series),
        dedup_method=dedup_method,
        resample_rule=resample_rule,
        resample_method=resample_method,
    )
