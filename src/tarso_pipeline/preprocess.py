"""Head series pre-processing before the Tarso fit (spec section 3)."""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from tarso_pipeline.io import HeadFormat, LoadedHead

# Aggregations allowed for resampling. An allowlist keeps a job parameter from
# turning into arbitrary attribute access on the resampler.
ALLOWED_METHODS = ("mean", "median", "min", "max", "first", "last", "sum")


@dataclass(frozen=True)
class PreparedHead:
    series: pd.Series  # resampled, NaN-free, ready for Pastas
    skipped: bool
    skip_reason: str  # empty when not skipped


def span_days(series: pd.Series) -> int:
    """Calendar days between the first and last observation (0 when < 2 points)."""
    if len(series) < 2:
        return 0
    return int((series.index.max() - series.index.min()).days)


def prepare_head(
    loaded: LoadedHead,
    rule: str = "D",
    method: str = "mean",
    min_obs: int = 100,
    min_days: int = 0,
) -> PreparedHead:
    """Trim, resample and apply the skip rules.

    1. Trim to the first/last valid observation.
    2. Resample with ``rule``/``method`` (default daily mean), then ``dropna()``:
       resampling a series with gaps creates empty buckets and Pastas must not
       receive NaN rows. Gaps are NOT interpolated or filled.
    3. Skip when fewer than ``min_obs`` observations remain, for validated files
       only (spec section 3).
    4. Skip when the series spans fewer than ``min_days`` calendar days, for
       **every** format. ``min_days=0`` disables it; 365 means "at least a year".
       A Tarso fit needs to see the seasonal cycle, so a short series produces a
       number that looks like a result but is not one.

    A skipped series is reported, never silently dropped.
    """
    if method not in ALLOWED_METHODS:
        raise ValueError(f"Unsupported resample method {method!r}; use one of {ALLOWED_METHODS}")
    if min_days < 0:
        raise ValueError(f"min_days must be >= 0, got {min_days}")

    series = loaded.series
    if series.first_valid_index() is not None:
        series = series.loc[series.first_valid_index() : series.last_valid_index()]

    resampled = getattr(series.resample(rule), method)().dropna()
    resampled.name = series.name

    if resampled.empty:
        # Distinct from the duration skip below: "spans 0 days" would be a
        # misleading diagnosis for a file that yielded no usable values at all
        # (every row rejected by the verdict filter, or not a head series).
        return PreparedHead(
            series=resampled,
            skipped=True,
            skip_reason="no usable observations after loading and resampling",
        )

    if loaded.format is HeadFormat.VALIDATED and len(resampled) < min_obs:
        return PreparedHead(
            series=resampled,
            skipped=True,
            skip_reason=f"only {len(resampled)} observations after resampling (min_obs={min_obs})",
        )

    span = span_days(resampled)
    if min_days and span < min_days:
        return PreparedHead(
            series=resampled,
            skipped=True,
            skip_reason=f"series spans {span} days (min_days={min_days})",
        )
    return PreparedHead(series=resampled, skipped=False, skip_reason="")
