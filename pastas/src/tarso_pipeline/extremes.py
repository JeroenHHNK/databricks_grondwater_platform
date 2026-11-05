"""Annual block extremes from simulated head series (handoff section 2).

One annual minimum and one annual maximum per hydrological year per member,
pooled across members. The three rules that make the pool valid:

**The block is a hydrological year, April-March.** A Dutch winter runs October
into March, so a calendar block cuts it in half: the peak of winter Y/Y+1 lands
in block Y if it falls in December and block Y+1 if it falls in January. One
winter would then supply the maximum of two adjacent blocks while its neighbour
supplies none -- exactly the dependence between block maxima a GEV assumes away.
The summer drought minimum is unaffected either way.

**Partial blocks are dropped.** A block covering less than 90% of a year cannot
yield an annual extreme: the maximum over three months is not an annual maximum.
This removes the first and last partial year of each member.

**Members are pooled at the extremes, never in time.** Each member is a separate
realisation of 1951-2000. Concatenating the daily series end to end would invent
continuity across every join -- December 2000 of member 1 followed by January
1951 of member 2 -- and the model's multi-year memory would carry a transition no
climate produced. So each member is reduced to one extreme per year first, and
only those extremes are pooled.

Expect **704 blocks per tail** from 16 members: ~44 usable years each. 800 means
the partial-block filter is not running; 16 440 means the members were
concatenated in time.
"""

from __future__ import annotations

import pandas as pd

# A block must span at least this fraction of a year to count as annual.
MIN_COVERAGE = 0.9

# Hydrological year starts in April.
YEAR_START_MONTH = 4

DAYS_PER_YEAR = 365.0


def hydrological_year(index: pd.DatetimeIndex, start_month: int = YEAR_START_MONTH):
    """Label each timestamp with its hydrological year.

    Year Y runs from April Y to March Y+1, so January 2000 belongs to year 1999.
    """
    if not 1 <= start_month <= 12:
        raise ValueError(f"start_month must be 1..12, got {start_month}")
    return index.year - (index.month < start_month).astype(int)


def block_coverage(series: pd.Series, start_month: int = YEAR_START_MONTH) -> pd.Series:
    """Fraction of a year each block spans, by hydrological year."""
    labels = hydrological_year(pd.DatetimeIndex(series.index), start_month)
    grouped = pd.Series(series.index, index=labels).groupby(level=0)
    span = grouped.max() - grouped.min()
    return (span.dt.days + 1) / DAYS_PER_YEAR


def annual_extremes(
    series: pd.Series,
    start_month: int = YEAR_START_MONTH,
    min_coverage: float = MIN_COVERAGE,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """One minimum and one maximum per complete hydrological year.

    Returns ``(dry, wet)``. Each frame is indexed by the **date the extreme
    occurred** and carries the value plus its hydrological ``year`` -- the date
    matters for auditing which storm or drought produced a given block maximum.
    """
    if series.empty:
        empty_dry = pd.DataFrame({"head_min": [], "year": []})
        empty_wet = pd.DataFrame({"head_max": [], "year": []})
        return empty_dry, empty_wet

    clean = series.dropna().sort_index()
    labels = hydrological_year(pd.DatetimeIndex(clean.index), start_month)
    labelled = pd.Series(clean.to_numpy(), index=clean.index)
    grouper = pd.Series(labels, index=clean.index)

    coverage = block_coverage(clean, start_month)
    complete = set(coverage[coverage >= min_coverage].index)

    dry_rows, wet_rows = [], []
    for year, group in labelled.groupby(grouper):
        if year not in complete:
            continue
        dry_rows.append((group.idxmin(), float(group.min()), int(year)))
        wet_rows.append((group.idxmax(), float(group.max()), int(year)))

    dry = pd.DataFrame(
        {"head_min": [v for _, v, _ in dry_rows], "year": [y for _, _, y in dry_rows]},
        index=pd.DatetimeIndex([d for d, _, _ in dry_rows], name="date"),
    )
    wet = pd.DataFrame(
        {"head_max": [v for _, v, _ in wet_rows], "year": [y for _, _, y in wet_rows]},
        index=pd.DatetimeIndex([d for d, _, _ in wet_rows], name="date"),
    )
    return dry, wet


def pool_members(per_member: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Stack each member's extremes into one sample, tagged with ``member``.

    Pooling happens here and only here. The frames going in are already one row
    per year per member, so nothing is being joined in time.
    """
    frames = []
    for member, frame in sorted(per_member.items()):
        if frame is None or frame.empty:
            continue
        tagged = frame.copy()
        tagged["member"] = member
        frames.append(tagged)
    if not frames:
        return pd.DataFrame()
    pooled = pd.concat(frames)
    pooled.index.name = "date"
    return pooled


def value_column(pooled: pd.DataFrame) -> str:
    """Whichever of head_min / head_max this pooled frame carries."""
    for name in ("head_min", "head_max"):
        if name in pooled.columns:
            return name
    raise ValueError(f"No head_min/head_max column in {list(pooled.columns)}")
