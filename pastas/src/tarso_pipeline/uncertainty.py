"""How sure the extremes are: the bootstrap, kept out of the main run.

``run_extremes`` fits and writes the return levels. Everything that needs hundreds of
refits lives here instead, and runs from
``notebooks/analysis/explore_shape_and_uncertainty.py`` when wanted:

- the **bootstrap SE of the GPD shape**, next to the asymptotic one the threshold plot
  draws;
- the **95% band of the GPD and exponential return levels**, per return period and as
  the ``confidence_plots`` figure.

It reads the ``pooled_extremes/<series>_{dry,wet}.csv`` that ``run_extremes`` wrote:
nothing is simulated again.

What this band does **not** contain is the uncertainty of the Pastas model itself: its
parameters are fitted on a few years of heads and taken as exact here. That part is
probably the larger one; it would need simulations with sampled model parameters.
"""

from __future__ import annotations

import warnings
from pathlib import Path

import numpy as np
import pandas as pd

from tarso_pipeline.evt import (
    BOOTSTRAP_REPLICATES,
    DEFAULT_T_LIST,
    TRIMMED_FAMILIES,
    as_maxima,
    bootstrap_params,
    fit_family,
    from_maxima,
    gpd_shape_se,
    tail_threshold,
    trim_tail,
)
from tarso_pipeline.evt_report import TAILS, VALUE_COLUMN

CONFIDENCE = 0.95

SHAPE_UNCERTAINTY_COLUMNS = [
    "run_id", "meetreeks_id", "series_name", "model_type", "tail", "exclude_first",
    "n_blocks", "n_kept", "threshold", "gpd_shape", "gpd_shape_se_asymptotic",
    "gpd_shape_se_bootstrap", "n_resamples",
]
RETURN_LEVEL_CI_COLUMNS = [
    "run_id", "meetreeks_id", "series_name", "model_type", "tail", "family", "return_period",
    "level", "ci_low", "ci_high", "ci_width", "n_resamples",
]


def family_levels(params, family: str, return_periods, lam: float = 1.0) -> np.ndarray:
    """Return levels in **maxima** space, vectorised over ``return_periods``.

    ``params`` is one parameter tuple or an ``(n, k)`` array of them; the result is
    ``(len(T),)`` or ``(n, len(T))``. NaN where ``lam * T <= 1``, as in
    :func:`tarso_pipeline.evt.return_level`.
    """
    from scipy.stats import expon, genextreme, genpareto

    dist = {"gev": genextreme, "exp": expon, "gpd": genpareto}[family]
    periods = np.asarray(return_periods, dtype=float)
    probability = 1.0 / (lam * periods)
    quantile = np.where(probability < 1.0, 1.0 - probability, np.nan)
    params = np.asarray(params, dtype=float)
    if params.ndim == 1:
        return dist.ppf(quantile, *params)
    columns = [params[:, [i]] for i in range(params.shape[1])]
    return dist.ppf(quantile[None, :], *columns)


def band(resamples: np.ndarray, family: str, return_periods, lam: float = 1.0,
         confidence: float = CONFIDENCE) -> tuple[np.ndarray, np.ndarray]:
    """Lower and upper percentile of the resampled levels, in maxima space."""
    periods = np.asarray(return_periods, dtype=float)
    if len(resamples) == 0:
        nan = np.full(len(periods), np.nan)
        return nan, nan.copy()
    curves = family_levels(resamples, family, periods, lam)
    edge = (1.0 - confidence) / 2 * 100
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)  # all-NaN columns below 1/lam
        return np.nanpercentile(curves, edge, axis=0), np.nanpercentile(curves, 100 - edge, axis=0)


def tail_uncertainty(
    values: np.ndarray,
    tail: str,
    exclude_first: int = 0,
    t_list=DEFAULT_T_LIST,
    replicates: int = BOOTSTRAP_REPLICATES,
    seed: int = 0,
) -> dict | None:
    """One tail's bootstrap: the GPD shape SE both ways, and the band per family.

    ``values`` are the pooled extremes in head units. ``None`` when nothing fits.
    """
    maxima = as_maxima(values, tail)
    kept, lam = trim_tail(maxima, exclude_first)
    threshold = tail_threshold(maxima, exclude_first)
    periods = np.asarray(t_list, dtype=float)
    levels, resamples, shape = [], {}, float("nan")
    for family in TRIMMED_FAMILIES:
        try:
            params = fit_family(kept, family, threshold)
        except Exception:
            continue
        if family == "gpd":
            shape = float(params[0])
        fits = bootstrap_params(kept, family, replicates=replicates, seed=seed, threshold=threshold)
        resamples[family] = fits
        central = family_levels(params, family, periods, lam)
        low, high = band(fits, family, periods, lam)
        # Back into head units. For the dry tail that flips the sign, so the bounds swap.
        central, low, high = (from_maxima(a, tail) for a in (central, low, high))
        low, high = np.minimum(low, high), np.maximum(low, high)
        levels.append(pd.DataFrame({"family": family, "return_period": [int(t) for t in t_list],
                                    "level": central, "ci_low": low, "ci_high": high,
                                    "ci_width": high - low, "n_resamples": len(fits)}))
    if not resamples:
        return None
    gpd_fits = resamples.get("gpd", np.empty((0, 3)))
    return {
        "summary": {
            "tail": tail, "exclude_first": exclude_first, "n_blocks": len(maxima), "n_kept": len(kept),
            "threshold": float(from_maxima(threshold, tail)), "gpd_shape": shape,
            "gpd_shape_se_asymptotic": gpd_shape_se(shape, len(kept)),
            "gpd_shape_se_bootstrap": float(np.std(gpd_fits[:, 0])) if len(gpd_fits) > 1 else float("nan"),
            "n_resamples": len(gpd_fits),
        },
        "levels": pd.concat(levels, ignore_index=True),
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
        for row in result["levels"].to_dict("records"):
            level_rows.append({**keys, "tail": tail,
                               **{k: (None if isinstance(v, float) and np.isnan(v) else v) for k, v in row.items()}})
    return shape_rows, level_rows, per_tail
