"""How sure the extremes stage can be: the GEV bootstrap, kept out of the main run.

``run_extremes`` fits, chooses a family with the Hessian SE of the shape and writes
the return levels. Everything that needs hundreds of refits lives here instead, and
runs from ``notebooks/analysis/explore_shape_and_uncertainty.py`` when wanted:

- the **bootstrap SE of the shape**, as a check on the Hessian SE the family choice
  used, and the family it would have chosen;
- the **95% band of the GEV return level**, per return period and as the
  ``confidence_plots`` figure.

Both come from one array of resampled GEV fits, so one bootstrap serves both. It
reads the ``pooled_extremes/<series>_{dry,wet}.csv`` that ``run_extremes`` wrote:
nothing is simulated again.
"""

from __future__ import annotations

import warnings
from pathlib import Path

import numpy as np
import pandas as pd

from tarso_pipeline.evt import (
    BOOTSTRAP_REPLICATES,
    DEFAULT_T_LIST,
    as_maxima,
    bootstrap_gev_params,
    from_maxima,
    hessian_shape_se,
    select_family,
    trim_tail,
)
from tarso_pipeline.evt_report import TAILS, VALUE_COLUMN

CONFIDENCE = 0.95

SHAPE_UNCERTAINTY_COLUMNS = [
    "run_id", "meetreeks_id", "series_name", "model_type", "tail", "exclude_first",
    "n_blocks", "n_fitted", "shape", "loc", "scale",
    "shape_se_hessian", "shape_se_bootstrap", "se_ratio", "n_resamples",
    "family_hessian", "verdict_hessian", "family_bootstrap", "verdict_bootstrap",
    "families_agree",
]
RETURN_LEVEL_CI_COLUMNS = [
    "run_id", "meetreeks_id", "series_name", "model_type", "tail", "return_period",
    "level", "ci_low", "ci_high", "ci_width", "n_resamples",
]


def gev_levels(params, return_periods, lam: float = 1.0) -> np.ndarray:
    """GEV return levels in **maxima** space, vectorised over ``return_periods``.

    ``params`` is one ``(shape, loc, scale)`` or an ``(n, 3)`` array of them; the
    result is ``(len(T),)`` or ``(n, len(T))``. NaN where ``lam * T <= 1``, as in
    :func:`tarso_pipeline.evt.return_level`.
    """
    from scipy.stats import genextreme

    periods = np.asarray(return_periods, dtype=float)
    probability = 1.0 / (lam * periods)
    quantile = np.where(probability < 1.0, 1.0 - probability, np.nan)
    params = np.asarray(params, dtype=float)
    if params.ndim == 1:
        return genextreme.ppf(quantile, *params)
    return genextreme.ppf(quantile[None, :], params[:, [0]], params[:, [1]], params[:, [2]])


def gev_band(
    resamples: np.ndarray,
    return_periods,
    lam: float = 1.0,
    confidence: float = CONFIDENCE,
) -> tuple[np.ndarray, np.ndarray]:
    """Lower and upper percentile of the resampled GEV levels, in maxima space."""
    periods = np.asarray(return_periods, dtype=float)
    if len(resamples) == 0:
        nan = np.full(len(periods), np.nan)
        return nan, nan.copy()
    curves = gev_levels(resamples, periods, lam)
    tail = (1.0 - confidence) / 2 * 100
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)  # all-NaN columns below 1/lam
        low = np.nanpercentile(curves, tail, axis=0)
        high = np.nanpercentile(curves, 100 - tail, axis=0)
    return low, high


def tail_uncertainty(
    values: np.ndarray,
    tail: str,
    exclude_first: int = 0,
    t_list=DEFAULT_T_LIST,
    replicates: int = BOOTSTRAP_REPLICATES,
    seed: int = 0,
) -> dict | None:
    """One tail's bootstrap: shape SE both ways, both family picks, the level band.

    ``values`` are the pooled extremes in head units (``head_min`` or
    ``head_max``). Returns ``None`` when the tail cannot be fitted at all; the
    guard rails of :func:`select_family` (too few blocks, a capped head) show in
    the verdicts instead.
    """
    from scipy.stats import genextreme

    maxima = as_maxima(values, tail)
    kept, lam = trim_tail(maxima, exclude_first)
    try:
        start = tuple(float(v) for v in genextreme.fit(kept))
    except Exception:
        return None

    resamples = bootstrap_gev_params(kept, replicates=replicates, seed=seed, start=start)
    se_boot = float(np.std(resamples[:, 0])) if len(resamples) > 1 else float("nan")
    se_hess = hessian_shape_se(kept, start)

    # What run_extremes chose: the Hessian SE, or the bootstrap where that is unusable.
    by_hessian = select_family(maxima, exclude_first=exclude_first, se_method="hessian",
                               shape_se=se_hess if np.isfinite(se_hess) else se_boot)
    by_bootstrap = select_family(maxima, exclude_first=exclude_first, se_method="bootstrap",
                                 shape_se=se_boot)

    periods = np.asarray(t_list, dtype=float)
    central = gev_levels(start, periods, lam)
    low, high = gev_band(resamples, periods, lam)
    # Back into head units. For the dry tail that flips the sign, so the bounds swap.
    central, low, high = (from_maxima(a, tail) for a in (central, low, high))
    low, high = np.minimum(low, high), np.maximum(low, high)

    return {
        "summary": {
            "tail": tail,
            "exclude_first": exclude_first,
            "n_blocks": len(maxima),
            "n_fitted": len(kept),
            "shape": start[0], "loc": start[1], "scale": start[2],
            "shape_se_hessian": se_hess,
            "shape_se_bootstrap": se_boot,
            "se_ratio": se_hess / se_boot if se_boot and np.isfinite(se_hess) else float("nan"),
            "n_resamples": len(resamples),
            "family_hessian": by_hessian.family,
            "verdict_hessian": by_hessian.verdict,
            "family_bootstrap": by_bootstrap.family,
            "verdict_bootstrap": by_bootstrap.verdict,
            "families_agree": by_hessian.family == by_bootstrap.family,
        },
        "levels": pd.DataFrame(
            {"level": central, "ci_low": low, "ci_high": high, "ci_width": high - low},
            index=pd.Index([int(t) for t in t_list], name="return_period"),
        ),
        "resamples": resamples,
        "lam": lam,
    }


def read_pooled(directory: str | Path, series_name: str) -> dict[str, np.ndarray]:
    """The pooled extremes ``run_extremes`` wrote for one model, per tail (head units)."""
    out = {}
    for tail in TAILS:
        path = Path(directory) / f"{series_name}_{tail}.csv"
        if path.is_file():
            frame = pd.read_csv(path)
            column = VALUE_COLUMN[tail]
            if column in frame.columns and not frame.empty:
                out[tail] = frame[column].dropna().to_numpy(dtype=float)
    return out


def pooled_series(directory: str | Path) -> list[str]:
    """Every series with at least one pooled tail in ``pooled_extremes/``."""
    names = set()
    for path in Path(directory).glob("*.csv"):
        for tail in TAILS:
            suffix = f"_{tail}"
            if path.stem.endswith(suffix):
                names.add(path.stem[: -len(suffix)])
    return sorted(names)


def model_uncertainty(
    pooled: dict[str, np.ndarray],
    meetreeks_id: str,
    series_name: str,
    run_id: str,
    exclude_first: int = 0,
    t_list=DEFAULT_T_LIST,
    replicates: int = BOOTSTRAP_REPLICATES,
    seed: int = 0,
    model_type: str = "tarso",
) -> tuple[list[dict], list[dict], dict]:
    """Both tails of one model: (shape rows, level-band rows, per-tail results)."""
    shape_rows, level_rows, per_tail = [], [], {}
    keys = {"run_id": run_id, "meetreeks_id": meetreeks_id,
            "series_name": series_name, "model_type": model_type}
    for tail in TAILS:
        if tail not in pooled:
            continue
        result = tail_uncertainty(pooled[tail], tail, exclude_first=exclude_first,
                                  t_list=t_list, replicates=replicates, seed=seed)
        if result is None:
            continue
        per_tail[tail] = result
        shape_rows.append({**keys, **result["summary"]})
        for period, row in result["levels"].iterrows():
            level_rows.append({
                **keys, "tail": tail, "return_period": int(period),
                **{k: None if pd.isna(v) else float(v) for k, v in row.items()},
                "n_resamples": result["summary"]["n_resamples"],
            })
    return shape_rows, level_rows, per_tail
