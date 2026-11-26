"""Drought indicators from precipitation and evaporation alone (so IMPREX can rebuild them).

Every indicator is a deterministic function of daily P and E in mm/day and returns a
non-negative series in mm: zero when it is wet, growing in a drought. A model gets it as an
extra stress model; for the return periods the same function runs on each IMPREX member
(``simulate_member(synthetic=...)``), so nothing is held at a mean.

``deficit``   the KNMI potential precipitation deficit: the running sum of E - P from 1 April,
              never below 0, zero from 1 October to 31 March; the stress is ``max(0, D - D0)``.
``rolling``   E - P summed over the last ``days`` days, clipped to the dry side (no reset).
``bucket``    a root-zone bucket: storage S in [0, Smax], actual ET = E * min(1, S / (lp Smax)),
              overflow leaves; the stress is the deficit ``Smax - S``. It starts full.

Variant parts (``research/accepted/variants.py``): ``def<D0>`` (``def0``, ``def50``, ...),
``roll<days>`` (``roll30``), ``bkt<Smax>l<lp*10>`` (``bkt100l5`` = Smax 100 mm, lp 0.5).
"""

from __future__ import annotations

import re

import numpy as np
import pandas as pd

APRIL, OCTOBER = 4, 10


def _align(precipitation: pd.Series, evaporation: pd.Series) -> tuple[pd.Series, pd.Series]:
    precipitation, evaporation = precipitation.align(evaporation, join="inner")
    return precipitation.fillna(0.0), evaporation.fillna(0.0)


def deficit(precipitation: pd.Series, evaporation: pd.Series, threshold: float = 0.0) -> pd.Series:
    """The KNMI potential precipitation deficit above ``threshold`` mm (0 outside April-September)."""
    precipitation, evaporation = _align(precipitation, evaporation)
    net = (evaporation - precipitation).to_numpy(dtype=float)
    months = precipitation.index.month
    out = np.zeros(len(net))
    level = 0.0
    for i, value in enumerate(net):
        if months[i] < APRIL or months[i] >= OCTOBER:
            level = 0.0
        else:
            level = max(0.0, level + value)
        out[i] = level
    return pd.Series(np.maximum(out - threshold, 0.0), index=precipitation.index, name="deficit")


def rolling(precipitation: pd.Series, evaporation: pd.Series, days: int = 60) -> pd.Series:
    """E - P over the last ``days`` days, dry side only."""
    precipitation, evaporation = _align(precipitation, evaporation)
    total = (evaporation - precipitation).rolling(days, min_periods=1).sum()
    return total.clip(lower=0.0).rename("deficit")


def bucket(precipitation: pd.Series, evaporation: pd.Series, smax: float = 100.0, lp: float = 0.5,
           balance: bool = False):
    """The root-zone deficit ``Smax - S`` of a simple bucket; with ``balance`` also the fluxes.

    The pipeline's own function (``tarso_pipeline.inputs.bucket_deficit``), so the research
    and the pipeline cannot drift apart; only the defaults differ (the pipeline's are the
    chosen 150 mm and 0.7).
    """
    from tarso_pipeline.inputs import bucket_deficit

    out = bucket_deficit(precipitation, evaporation, smax=smax, lp=lp, balance=balance)
    if balance:
        return out[0].rename("deficit"), out[1]
    return out.rename("deficit")


PART = re.compile(r"^(?:def(?P<d0>\d+)|roll(?P<days>\d+)|bkt(?P<smax>\d+)l(?P<lp>\d))$")


def is_part(part: str) -> bool:
    return bool(PART.match(part))


def from_part(part: str, precipitation_mm: pd.Series, evaporation_mm: pd.Series) -> pd.Series:
    """The indicator a variant part names, from P and E in mm/day."""
    match = PART.match(part)
    if not match:
        raise ValueError(f"not a drought part: {part!r}")
    if match["d0"] is not None:
        return deficit(precipitation_mm, evaporation_mm, float(match["d0"]))
    if match["days"] is not None:
        return rolling(precipitation_mm, evaporation_mm, int(match["days"]))
    return bucket(precipitation_mm, evaporation_mm, float(match["smax"]), int(match["lp"]) / 10.0)
