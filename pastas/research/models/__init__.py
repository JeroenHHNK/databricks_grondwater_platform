"""Candidate models. Every one turns (training heads, forcing) into a simulated head.

Per-series methods: ``simulate(task, ctx) -> pd.Series`` on the days from the start of the
series to its end. Global methods (one model for all series):
``simulate_all(tasks, ctx) -> {meetreeks_id: pd.Series}``.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from research import features


@dataclass
class Context:
    precipitation: pd.Series  # m/day, for Pastas
    evaporation: pd.Series
    forcing: pd.DataFrame     # mm/day
    regression: pd.DataFrame  # features.regression_features
    ml: pd.DataFrame          # features.ml_features
    sequence: pd.DataFrame    # features.sequence_inputs

    @classmethod
    def build(cls, precipitation: pd.Series, evaporation: pd.Series) -> "Context":
        forcing = features.forcing_frame(precipitation, evaporation)
        return cls(precipitation, evaporation, forcing, features.regression_features(forcing),
                   features.ml_features(forcing), features.sequence_inputs(forcing))


def days(head: pd.Series) -> pd.DatetimeIndex:
    return pd.date_range(head.index[0], head.index[-1], freq="D")
