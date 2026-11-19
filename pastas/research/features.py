"""Inputs built from precipitation and evaporation only: what every candidate model sees.

Every column at day ``t`` uses forcing up to and including ``t`` and nothing later, so a
model on these features simulates from the forcing alone, as Pastas does on IMPREX.
The forcing comes in m/day (``load_forcing_pair(units="m")``) and is turned into mm/day,
a scale the regressions and the networks handle better.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

WINDOWS = (1, 3, 7, 14, 30, 60, 90, 180, 365)  # days, rolling sums ending at t
API_DECAYS = (10, 30, 100, 300)                # days, exponential memory
NET_WINDOWS = (3, 7, 14, 30, 60, 90, 180, 365)  # the smaller set for the regressions


def forcing_frame(precipitation: pd.Series, evaporation: pd.Series) -> pd.DataFrame:
    """Daily P and E in mm/day on one gap-free index."""
    frame = pd.DataFrame({"P": precipitation, "E": evaporation}).sort_index()
    frame = frame.asfreq("D")
    if frame.isna().any().any():
        raise ValueError("forcing has gaps; load it with load_forcing_pair")
    return frame * 1000.0


def decayed_sum(values: np.ndarray, decay_days: float) -> np.ndarray:
    """a_t = a_{t-1} * exp(-1/k) + x_t: an antecedent index with memory ``k`` days."""
    factor = np.exp(-1.0 / decay_days)
    out = np.empty_like(values, dtype=float)
    running = 0.0
    for i, value in enumerate(values):
        running = running * factor + value
        out[i] = running
    return out


def seasonal(index: pd.DatetimeIndex, harmonics: int = 2) -> pd.DataFrame:
    angle = 2 * np.pi * index.day_of_year.to_numpy() / 365.25
    columns = {}
    for k in range(1, harmonics + 1):
        columns[f"sin{k}"] = np.sin(k * angle)
        columns[f"cos{k}"] = np.cos(k * angle)
    return pd.DataFrame(columns, index=index)


def ml_features(forcing: pd.DataFrame) -> pd.DataFrame:
    """The full set for the tree models: P, E and P-E window sums, decayed indices, season."""
    net = forcing["P"] - forcing["E"]
    columns = {}
    for w in WINDOWS:
        columns[f"P_{w}"] = forcing["P"].rolling(w, min_periods=w).sum()
        columns[f"E_{w}"] = forcing["E"].rolling(w, min_periods=w).sum()
        columns[f"N_{w}"] = net.rolling(w, min_periods=w).sum()
    for k in API_DECAYS:
        columns[f"apiP_{k}"] = decayed_sum(forcing["P"].to_numpy(), k)
        columns[f"apiN_{k}"] = decayed_sum(net.to_numpy(), k)
    frame = pd.DataFrame(columns, index=forcing.index).join(seasonal(forcing.index))
    return frame.dropna()


def regression_features(forcing: pd.DataFrame) -> pd.DataFrame:
    """The smaller set for the ARX models: net recharge sums, plus season and a constant.

    Twelve columns instead of fifty: with about 500 daily heads a linear model on every
    window would mostly fit collinearity.
    """
    net = forcing["P"] - forcing["E"]
    columns = {f"N_{w}": net.rolling(w, min_periods=w).sum() for w in NET_WINDOWS}
    frame = pd.DataFrame(columns, index=forcing.index).join(seasonal(forcing.index))
    frame.insert(0, "const", 1.0)
    return frame.dropna()


def sequence_inputs(forcing: pd.DataFrame) -> pd.DataFrame:
    """Per-day inputs for the networks: P, E (standardised) and the season."""
    scaled = (forcing - forcing.mean()) / forcing.std()
    return scaled.join(seasonal(forcing.index, harmonics=1))
