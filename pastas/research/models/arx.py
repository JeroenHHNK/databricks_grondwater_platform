"""Box-Jenkins transfer-function models with P and E as exogenous inputs.

``dlm_ols``      distributed-lag regression: head = X b, X = net recharge (P - E) summed over
                 3..365 days, two annual Fourier pairs and a constant; ordinary least squares.
``dlm_sarimax``  the same regression with ARMA(p, q) errors (statsmodels ``SARIMAX``: a
                 regression with ARIMA errors), p in {1, 2}, q in {0, 1} by AIC. The errors
                 cannot be known without measured heads, so the simulation is X b: the
                 deterministic transfer function, as Pastas drops its noise model.
``arx``          ARX(p): h_t = sum a_i h_(t-i) + X_t b, fitted by least squares on the
                 observed days and *simulated recursively* on its own output (Knotters &
                 Bierkens). p in {1, 2, 3} is chosen on the last 20 % of the training block
                 by simulation R2; a p whose AR polynomial is unstable is rejected. Seasonal
                 ARIMA with s = 365 on daily data is replaced by the Fourier terms.
"""

from __future__ import annotations

import warnings

import numpy as np
import pandas as pd

from research.evaluate import Task, inner_split, r2
from research.models import Context, days

SARIMAX_ORDERS = ((1, 0, 0), (2, 0, 0), (1, 0, 1), (2, 0, 1))
ARX_ORDERS = (1, 2, 3)


def _scaled(X: pd.DataFrame, rows: pd.Index) -> pd.DataFrame:
    """Standardise every column but the constant on the training rows."""
    cols = [c for c in X.columns if c != "const"]
    mean, std = X.loc[rows, cols].mean(), X.loc[rows, cols].std().replace(0, 1.0)
    out = X.copy()
    out[cols] = (X[cols] - mean) / std
    return out


def simulate_dlm_ols(task: Task, ctx: Context) -> pd.Series:
    train = task.train
    X = _scaled(ctx.regression, train.index)
    beta, *_ = np.linalg.lstsq(X.loc[train.index].to_numpy(), train.to_numpy(), rcond=None)
    index = days(task.series.head)
    return pd.Series(X.loc[index].to_numpy() @ beta, index=index)


def simulate_dlm_sarimax(task: Task, ctx: Context) -> pd.Series:
    from statsmodels.tsa.statespace.sarimax import SARIMAX

    train = task.train.asfreq("D")  # gaps become NaN; the Kalman filter skips them
    X = _scaled(ctx.regression, task.train.index)
    best = None
    for order in SARIMAX_ORDERS:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            try:
                fit = SARIMAX(train, exog=X.loc[train.index], order=order, trend="n").fit(disp=False, maxiter=200)
            except Exception:  # noqa: BLE001 - an order that does not converge is just not a candidate
                continue
        if np.isfinite(fit.aic) and (best is None or fit.aic < best.aic):
            best = fit
    if best is None:
        raise RuntimeError("no SARIMAX order converged")
    beta = best.params[list(X.columns)].to_numpy()
    index = days(task.series.head)
    return pd.Series(X.loc[index].to_numpy() @ beta, index=index)


def stable(ar: np.ndarray) -> bool:
    """All roots of 1 - a1 z - ... - ap z^p outside the unit circle."""
    roots = np.roots(np.r_[-ar[::-1], 1.0])
    return bool(np.all(np.abs(roots) > 1.0))


def _fit_arx(head: pd.Series, X: pd.DataFrame, p: int) -> np.ndarray:
    y = head.asfreq("D")
    design = pd.concat({f"y{i}": y.shift(i) for i in range(1, p + 1)}, axis=1).join(X.loc[y.index])
    ok = design.notna().all(axis=1) & y.notna()
    if ok.sum() < 30:
        raise ValueError("too few days with all lags observed")
    beta, *_ = np.linalg.lstsq(design[ok].to_numpy(), y[ok].to_numpy(), rcond=None)
    return beta


def simulate_arx_from(beta: np.ndarray, p: int, X: pd.DataFrame, head: pd.Series,
                      index: pd.DatetimeIndex) -> pd.Series:
    """Run the ARX on its own output from the first ``p`` consecutive observed days of ``head``.

    Only those ``p`` starting values are measured; every later head is simulated.
    """
    y = head.asfreq("D")
    observed = y.notna().to_numpy()
    start = next((k for k in range(p, len(y)) if observed[k - p:k].all()), None)
    if start is None:
        raise ValueError("no p consecutive observed days to start from")
    a, b = beta[:p], beta[p:]
    first = y.index[start]
    steps = index[index >= first]
    drive = X.loc[steps].to_numpy() @ b
    history = list(y.to_numpy()[start - p:start])
    out = np.empty(len(steps))
    for t in range(len(steps)):
        value = drive[t] + sum(a[i] * history[-1 - i] for i in range(p))
        out[t] = value
        history.append(value)
    return pd.Series(out, index=steps).reindex(index)


def simulate_arx(task: Task, ctx: Context) -> pd.Series:
    X = _scaled(ctx.regression, task.train.index)
    fit_part, check_part = inner_split(task.train)
    best, best_score = None, -np.inf
    for p in ARX_ORDERS:
        try:
            beta = _fit_arx(fit_part, X, p)
            if not stable(beta[:p]):
                continue
            score = r2(check_part, simulate_arx_from(beta, p, X, fit_part, days(task.train)))
        except ValueError:
            continue
        if score is not None and score > best_score:
            best, best_score = p, score
    if best is None:
        raise RuntimeError("no stable ARX order")
    beta = _fit_arx(task.train, X, best)
    if not stable(beta[:best]):
        raise RuntimeError(f"ARX({best}) unstable on the full training block")
    return simulate_arx_from(beta, best, X, task.train, days(task.series.head))
