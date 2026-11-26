"""Folds, the R2 every method is scored with, and the leak guard for global models.

Folds (one ``Task`` per series and fold):

``full``     fit on the whole series, scored on the whole series: the in-sample R2, what
             the pipeline stores as ``r2`` (Pastas ``rsq``).
``holdout``  fit on the observations up to the 70 % point, scored on the rest: exactly the
             split of ``tarso_pipeline.fit.holdout_r2`` (``r2_holdout``).
``cv50``     fit up to 50 %, scored on the next 20 % (50-70 %).
``cv60``     fit up to 60 %, scored on the next 20 % (60-80 %).

The CV R2 of a series is the mean of ``cv50``, ``cv60`` and ``holdout``: three origins
instead of one, because 30 % of a two-year series is mostly a single season.

Every prediction is a *simulation*: the model gets the training heads and the forcing, and
returns a head for every day from the start of the series to its end. Measured heads after
the split never go in.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from research.data import Series

FOLDS = {"full": (None, None), "holdout": (0.7, None), "cv50": (0.5, 0.7), "cv60": (0.6, 0.8)}
CV_FOLDS = ("cv50", "cv60", "holdout")
MIN_EVAL = 10  # as holdout_r2: fewer scored days -> no score


@dataclass(frozen=True)
class Task:
    series: Series
    fold: str
    train: pd.Series          # the heads the model may see
    evaluate: pd.Series       # the heads it is scored on
    split: pd.Timestamp | None  # last training day; None for the full fold

    @property
    def meetreeks_id(self) -> str:
        return self.series.meetreeks_id

    @property
    def tmax(self) -> str:
        return (self.split or self.series.head.index[-1]).strftime("%Y-%m-%d")


def make_task(series: Series, fold: str) -> Task:
    head = series.head
    start, end = FOLDS[fold]
    if start is None:
        return Task(series, fold, head, head, None)
    split = head.index[int(len(head) * start)]
    later = head[head.index > split]
    if end is not None:
        later = later[later.index <= head.index[int(len(head) * end)]]
    return Task(series, fold, head[head.index <= split], later, split)


def r2(observed: pd.Series, simulated: pd.Series) -> float | None:
    """1 - SSE/SST on the days both have: the formula of ``holdout_r2`` and Pastas ``rsq``."""
    observed, simulated = observed.align(simulated, join="inner")
    keep = simulated.notna() & observed.notna()
    observed, simulated = observed[keep], simulated[keep]
    spread = float(((observed - observed.mean()) ** 2).sum())
    if len(observed) < MIN_EVAL or spread == 0:
        return None
    return float(1 - ((observed - simulated) ** 2).sum() / spread)


def score(task: Task, simulated: pd.Series | None, method: str, seconds: float,
          error: str | None = None) -> dict:
    row = {"meetreeks_id": task.meetreeks_id, "series_name": task.series.name, "method": method,
           "fold": task.fold, "n_train": len(task.train), "n_eval": len(task.evaluate),
           "r2": None, "rmse": None, "bias": None, "fit_seconds": round(seconds, 2), "error": error}
    if simulated is None:
        return row
    row["r2"] = r2(task.evaluate, simulated)
    observed, sim = task.evaluate.align(simulated, join="inner")
    keep = sim.notna()
    if keep.sum():
        row["rmse"] = float(np.sqrt(((observed[keep] - sim[keep]) ** 2).mean()))
        row["bias"] = float((sim[keep] - observed[keep]).mean())
    return row


def global_training(tasks: list[Task]) -> dict[str, pd.Series]:
    """Training heads for a model shared by all series, cut at one calendar date.

    Nearly every series ends on the same day, so their test blocks fall in the same
    months. A shared model trained on each series' own training block would still see
    other series' heads in those months and learn the response to that weather. Cutting
    every series at the earliest split of the fold keeps all test months unseen.
    """
    splits = [t.split for t in tasks if t.split is not None]
    if not splits:
        return {t.meetreeks_id: t.train for t in tasks}
    cutoff = min(splits)
    return {t.meetreeks_id: t.train[t.train.index <= cutoff] for t in tasks}


def inner_split(train: pd.Series, share: float = 0.2) -> tuple[pd.Series, pd.Series]:
    """The last ``share`` of a training block, for tuning: the test block is never used."""
    cut = train.index[int(len(train) * (1 - share))]
    return train[train.index <= cut], train[train.index > cut]
