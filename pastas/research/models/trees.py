"""Tree ensembles on forcing features only (no head lags, so they simulate like Pastas).

``rf``           random forest per series; ``min_samples_leaf`` in {5, 20} chosen on the
                 last 20 % of the training block.
``lgbm``         gradient boosting (LightGBM) per series; the number of trees by early
                 stopping on that same inner block, then refitted on the whole block.
``lgbm_global``  one LightGBM model for all series: target = head minus the series' training
                 mean, plus the series as a categorical feature and its training spread.
                 Trained on the calendar-cut blocks of ``evaluate.global_training``.

Known limit: a tree cannot predict outside the range of its training heads. It cannot run
away on IMPREX, but it also cannot produce a level wetter or drier than it has seen.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from research.evaluate import Task, global_training, inner_split, r2
from research.models import Context, days

LGBM = dict(learning_rate=0.03, num_leaves=15, min_child_samples=20, subsample=0.8, subsample_freq=1,
            colsample_bytree=0.8, reg_lambda=1.0, verbose=-1, n_jobs=1)


def simulate_rf(task: Task, ctx: Context) -> pd.Series:
    from sklearn.ensemble import RandomForestRegressor

    X = ctx.ml
    fit_part, check_part = inner_split(task.train)

    def forest(leaf):
        return RandomForestRegressor(n_estimators=300, min_samples_leaf=leaf, max_features=0.33,
                                     random_state=0, n_jobs=1)

    scores = {}
    for leaf in (5, 20):
        model = forest(leaf).fit(X.loc[fit_part.index], fit_part.to_numpy())
        scores[leaf] = r2(check_part, pd.Series(model.predict(X.loc[check_part.index]), check_part.index)) or -np.inf
    model = forest(max(scores, key=scores.get)).fit(X.loc[task.train.index], task.train.to_numpy())
    index = days(task.series.head)
    return pd.Series(model.predict(X.loc[index]), index=index)


def simulate_lgbm(task: Task, ctx: Context) -> pd.Series:
    import lightgbm as lgb

    X = ctx.ml
    fit_part, check_part = inner_split(task.train)
    probe = lgb.LGBMRegressor(n_estimators=2000, **LGBM).fit(
        X.loc[fit_part.index], fit_part.to_numpy(), eval_set=[(X.loc[check_part.index], check_part.to_numpy())],
        callbacks=[lgb.early_stopping(50, verbose=False)])
    n_trees = max(int(probe.best_iteration_ or 100), 20)
    model = lgb.LGBMRegressor(n_estimators=n_trees, **LGBM).fit(X.loc[task.train.index], task.train.to_numpy())
    index = days(task.series.head)
    return pd.Series(model.predict(X.loc[index]), index=index)


def _global_rows(heads: dict[str, pd.Series], X: pd.DataFrame, stats: dict, codes: dict) -> tuple:
    frames, targets = [], []
    for sid, head in heads.items():
        mean, std = stats[sid]
        frame = X.loc[head.index].copy()
        frame["series"] = codes[sid]
        frame["spread"] = std
        frames.append(frame)
        targets.append(head.to_numpy() - mean)
    frame = pd.concat(frames)
    frame["series"] = frame["series"].astype("category")
    return frame, np.concatenate(targets)


def simulate_lgbm_global(tasks: list[Task], ctx: Context) -> dict[str, pd.Series]:
    import lightgbm as lgb

    X = ctx.ml
    heads = global_training(tasks)
    heads = {sid: h for sid, h in heads.items() if len(h) >= 30}
    codes = {sid: i for i, sid in enumerate(sorted(heads))}
    stats = {sid: (float(h.mean()), float(h.std())) for sid, h in heads.items()}
    fit_heads, check_heads = {}, {}
    for sid, head in heads.items():
        fit_heads[sid], check_heads[sid] = inner_split(head)
    params = {**LGBM, "n_jobs": -1, "num_leaves": 31}
    fit_X, fit_y = _global_rows(fit_heads, X, stats, codes)
    check_X, check_y = _global_rows(check_heads, X, stats, codes)
    check_X["series"] = pd.Categorical(check_X["series"], categories=fit_X["series"].cat.categories)
    probe = lgb.LGBMRegressor(n_estimators=4000, **params).fit(
        fit_X, fit_y, eval_set=[(check_X, check_y)], callbacks=[lgb.early_stopping(100, verbose=False)])
    n_trees = max(int(probe.best_iteration_ or 200), 50)
    all_X, all_y = _global_rows(heads, X, stats, codes)
    model = lgb.LGBMRegressor(n_estimators=n_trees, **params).fit(all_X, all_y)
    out = {}
    for task in tasks:
        sid = task.meetreeks_id
        if sid not in heads:
            continue
        index = days(task.series.head)
        frame = X.loc[index].copy()
        frame["series"] = pd.Categorical([codes[sid]] * len(index), categories=all_X["series"].cat.categories)
        frame["spread"] = stats[sid][1]
        out[sid] = pd.Series(model.predict(frame) + stats[sid][0], index=index)
    return out
