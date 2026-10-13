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


def prepare_head(
    loaded: LoadedHead,
    rule: str = "D",
    method: str = "mean",
    min_obs: int = 100,
) -> PreparedHead:
    """Trim, resample and (for validated files) apply the minimum-observation skip.

    1. Trim to the first/last valid observation.
    2. Resample with ``rule``/``method`` (default daily mean), then ``dropna()``:
       resampling a series with gaps creates empty buckets and Pastas must not
       receive NaN rows. Gaps are NOT interpolated or filled.
    3. Skip when fewer than ``min_obs`` observations remain, for validated files
       only. A skipped series is reported, never silently dropped.
    """
    if method not in ALLOWED_METHODS:
        raise ValueError(f"Unsupported resample method {method!r}; use one of {ALLOWED_METHODS}")

    series = loaded.series
    if series.first_valid_index() is not None:
        series = series.loc[series.first_valid_index() : series.last_valid_index()]

    resampled = getattr(series.resample(rule), method)().dropna()
    resampled.name = series.name

    if loaded.format is HeadFormat.VALIDATED and len(resampled) < min_obs:
        return PreparedHead(
            series=resampled,
            skipped=True,
            skip_reason=f"only {len(resampled)} observations after resampling (min_obs={min_obs})",
        )
    return PreparedHead(series=resampled, skipped=False, skip_reason="")
