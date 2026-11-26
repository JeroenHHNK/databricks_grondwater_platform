"""The baseline: the pipeline's own model types, built and solved exactly as the pipeline does.

``build_model`` from ``tarso_pipeline.fit``; solved on the training heads from the
calibration start (``tmin``) to the split; simulated from the first to the last day of the
series, as ``holdout_r2`` does. On the ``full`` fold this is ``fit_series``' fit.
"""

from __future__ import annotations

import pandas as pd

from research.evaluate import Task
from research.models import Context

MODEL_TYPES = ("tarso", "linear_exp", "linear_gamma", "linear_dexp", "linear_fourparam")


def simulate(task: Task, ctx: Context, model_type: str) -> pd.Series:
    from tarso_pipeline.fit import build_model

    head = task.series.head
    model = build_model(model_type, task.series.name, task.train, ctx.precipitation, ctx.evaporation)
    model.solve(tmin=task.series.tmin, tmax=task.tmax, report=False)
    return model.simulate(tmin=head.index[0], tmax=head.index[-1])
