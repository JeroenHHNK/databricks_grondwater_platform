"""Extreme-value fits and return levels of the pooled annual extremes (section 5).

Both tails are fitted as **maxima**: a drought minimum is a maximum of ``-head``.

What is used for what
---------------------
``emp``  the empirical level from the ~704 pooled annual extremes (Weibull plotting
         position). With 704 synthetic years a T=100 level has ~7 exceedances in the
         pool, so up to ``EMPIRICAL_MAX_T`` no distribution is needed: the levels that
         are quoted come from here.
``gpd``  generalised Pareto on the values **above a threshold** (the trimmed sample),
         lower bound fixed at that threshold. Above a high threshold the excesses
         follow a GPD whatever the distribution of the annual extremes
         (Pickands-Balkema-de Haan), so this is the coherent family for a trimmed fit.
         Used to extrapolate past ``EMPIRICAL_MAX_T``: a bounded tail (shape < 0)
         flattens off, as a groundwater head against drainage or surface level does.
``exp``  the GPD with shape 0: an exponential tail on the same threshold, which does
         not flatten off. The other choice for extrapolating.
``gev``  generalised extreme value on **all** annual extremes (no trimming), as a
         comparison only. A GEV belongs on the whole sample of block maxima; fitted to
         its top part only it pushes the shape towards an unbounded tail and over-
         states the T=1000 level by decimetres to metres.

Which of ``gpd`` / ``exp`` applies to a tail is picked by a person, from the plots
(``family_choice``); nothing here chooses.

Trimming
--------
``exclude_first`` drops the N least extreme values; the threshold sits between the
last dropped and the first kept value. What remains is conditional on clearing that
threshold: if a fraction ``lam = n_kept / n_total`` survives, a T-year event is the
``1 / (lam * T)`` quantile of the fit, **not** the ``1 / T`` one. When ``lam * T <= 1``
the T-year level sits below the threshold and is returned as NaN.

The threshold is a choice, so :func:`threshold_scan` fits the GPD for a range of
thresholds: where its shape stops changing, the threshold is high enough.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

FAMILIES = ("gpd", "exp", "gev")       # fitted and reported
TRIMMED_FAMILIES = ("gpd", "exp")       # fitted above the threshold; the ones to pick from
PICKABLE = TRIMMED_FAMILIES

DEFAULT_T_LIST = (2, 5, 10, 25, 50, 100, 250, 500, 1000)
# Up to this T the empirical level is used; past it the picked family extrapolates.
EMPIRICAL_MAX_T = 100
# From this T on a level is extrapolation regardless: report it as indicative.
INDICATIVE_MIN_T = 1000
# Numbers of values kept above the threshold, for the threshold scan.
DEFAULT_KEPT_GRID = (100, 150, 200, 250, 300, 350, 400)

# Fewer blocks than this and the tail is not resolvable; flag for review.
MIN_BLOCKS = 100

# Under a millimetre of variation across a yearly extreme is not signal. This is
# the Tarso failure mode: a model whose dmax ceiling caps the head produces 704
# identical annual maxima, which fit perfectly cleanly and meaninglessly.
MIN_SPREAD = 1e-3

BOOTSTRAP_REPLICATES = 200


def as_maxima(values: np.ndarray, tail: str) -> np.ndarray:
    """Both tails as maxima: a drought minimum is a maximum of ``-head``."""
    if tail not in ("dry", "wet"):
        raise ValueError(f"tail must be 'dry' or 'wet', got {tail!r}")
    values = np.asarray(values, dtype=float)
    return -values if tail == "dry" else values


def from_maxima(values, tail: str):
    """Undo :func:`as_maxima`, bringing a level back into head units."""
    return -np.asarray(values, dtype=float) if tail == "dry" else np.asarray(values, dtype=float)


def trim_tail(maxima: np.ndarray, exclude_first: int = 0) -> tuple[np.ndarray, float]:
    """Drop the ``exclude_first`` least extreme values; return (kept, lam).

    ``lam`` is the surviving fraction, which the return-level correction needs.
    """
    maxima = np.sort(np.asarray(maxima, dtype=float))
    n_total = len(maxima)
    if exclude_first < 0:
        raise ValueError(f"exclude_first must be >= 0, got {exclude_first}")
    if exclude_first >= n_total:
        raise ValueError(
            f"exclude_first={exclude_first} would leave nothing of {n_total} blocks"
        )
    kept = maxima[exclude_first:]
    return kept, len(kept) / n_total


def tail_threshold(maxima: np.ndarray, exclude_first: int = 0) -> float:
    """The threshold of a trimmed fit: midway between the last dropped and first kept value.

    Without trimming it is the smallest value, so every value is at or above it.
    """
    ordered = np.sort(np.asarray(maxima, dtype=float))
    if exclude_first <= 0:
        return float(ordered[0])
    if exclude_first >= len(ordered):
        raise ValueError(f"exclude_first={exclude_first} would leave nothing of {len(ordered)} blocks")
    return float((ordered[exclude_first - 1] + ordered[exclude_first]) / 2)


def fit_family(maxima: np.ndarray, family: str, threshold: float | None = None) -> tuple[float, ...]:
    """Fit one family; returns scipy's parameter tuple.

    ``gpd`` and ``exp`` take their lower bound (location) fixed at ``threshold``
    (default: the smallest value); ``gev`` estimates all three parameters.
    """
    from scipy.stats import expon, genextreme, genpareto

    maxima = np.asarray(maxima, dtype=float)
    if family == "gev":
        return genextreme.fit(maxima)
    location = float(np.min(maxima)) if threshold is None else float(threshold)
    if family == "exp":
        return expon.fit(maxima, floc=location)
    if family == "gpd":
        return genpareto.fit(maxima, floc=location)
    raise ValueError(f"Unknown family {family!r}; use one of {FAMILIES}")


def snap_exclude_first(n_total: int, exclude_first: int, kept_grid=DEFAULT_KEPT_GRID) -> int:
    """``exclude_first`` moved so the default threshold is one of the scan's: the grid value
    of ``n_kept`` nearest to ``n_total - exclude_first`` (704 - 450 = 254 -> 250 kept).

    Without a usable grid value the given ``exclude_first`` stands.
    """
    wanted = n_total - exclude_first
    grid = [int(k) for k in kept_grid if 0 < int(k) < n_total]
    if not grid:
        return exclude_first
    return n_total - min(grid, key=lambda k: (abs(k - wanted), k))


def gpd_shape_se(shape: float, n: int) -> float:
    """Asymptotic standard error of the GPD shape: ``(1 + shape) / sqrt(n)``.

    Valid for shape > -0.5 (Smith 1985); below that the fit is non-regular and NaN
    is returned.
    """
    if n <= 0 or not np.isfinite(shape) or shape <= -0.5:
        return float("nan")
    return float((1.0 + shape) / np.sqrt(n))


def exceedance_probability(return_period: float, lam: float = 1.0) -> float:
    """``1 / (lam * T)`` -- the trimming-corrected exceedance probability."""
    if return_period <= 0:
        raise ValueError(f"return period must be > 0, got {return_period}")
    if not 0 < lam <= 1:
        raise ValueError(f"lam must be in (0, 1], got {lam}")
    return 1.0 / (lam * return_period)


def return_level(
    params: tuple[float, ...],
    family: str,
    return_period: float,
    lam: float = 1.0,
) -> float:
    """Level exceeded once per ``return_period`` years, in **maxima** space.

    NaN when ``lam * T <= 1``: that level lies below the trim threshold and the
    trimmed sample says nothing about it.
    """
    from scipy.stats import expon, genextreme, genpareto

    probability = exceedance_probability(return_period, lam)
    if probability >= 1.0:
        return float("nan")

    quantile = 1.0 - probability
    dist = {"gev": genextreme, "exp": expon, "gpd": genpareto}[family]
    try:
        return float(dist.ppf(quantile, *params))
    except Exception:
        return float("nan")


def empirical_points(
    maxima_all: np.ndarray,
    exclude_first: int = 0,
) -> pd.DataFrame:
    """Empirical return periods by Weibull plotting position.

    Ranks are computed on the **untrimmed** sample -- the k-th largest of the
    original n blocks has return period ``(n+1)/k`` whatever was trimmed -- and
    the trimmed body is then dropped from the curve rather than renumbered.
    """
    maxima_all = np.sort(np.asarray(maxima_all, dtype=float))[::-1]  # descending
    n_total = len(maxima_all)
    rank = np.arange(1, n_total + 1)
    frame = pd.DataFrame(
        {
            "rank": rank,
            "value": maxima_all,
            "return_period": (n_total + 1) / rank,
        }
    )
    if exclude_first:
        frame = frame.iloc[: n_total - exclude_first]
    return frame.reset_index(drop=True)


def empirical_level(points: pd.DataFrame, return_period: float) -> float:
    """Empirical level at T, interpolated on the plotting positions (maxima space).

    NaN outside the range the sample reaches -- 704 blocks reach T~705, so a
    1000-year empirical level does not exist and is not invented.
    """
    if points.empty:
        return float("nan")
    ordered = points.sort_values("return_period")
    periods = ordered["return_period"].to_numpy()
    values = ordered["value"].to_numpy()
    if return_period < periods[0] or return_period > periods[-1]:
        return float("nan")
    return float(np.interp(return_period, periods, values))


def fit_all_families(
    maxima: np.ndarray,
    exclude_first: int = 0,
    t_list=DEFAULT_T_LIST,
) -> tuple[dict, pd.DataFrame]:
    """Fit the three families and tabulate their return levels (maxima space).

    ``gpd`` and ``exp`` on the values above the threshold of ``exclude_first``, ``gev``
    on every value. Returns ``(parameters_by_family, levels)`` where ``levels`` has one
    row per T and one column per family.
    """
    maxima = np.asarray(maxima, dtype=float)
    n_total = len(maxima)
    kept, lam = trim_tail(maxima, exclude_first)
    threshold = tail_threshold(maxima, exclude_first)
    parameters, columns = {}, {}
    for family in FAMILIES:
        sample, fraction = (maxima, 1.0) if family == "gev" else (kept, lam)
        try:
            params = fit_family(sample, family, None if family == "gev" else threshold)
        except Exception:
            parameters[family] = None
            columns[family] = [float("nan")] * len(t_list)
            continue
        parameters[family] = {
            "params": tuple(float(p) for p in params),
            "lam": fraction,
            "n_total": n_total,
            "n_fitted": len(sample),
            "exceedance_rate": fraction,
            "threshold": None if family == "gev" else threshold,
        }
        columns[family] = [return_level(params, family, T, fraction) for T in t_list]

    levels = pd.DataFrame(columns, index=pd.Index(t_list, name="return_period"))
    return parameters, levels


def threshold_scan(
    maxima: np.ndarray,
    kept_grid=DEFAULT_KEPT_GRID,
    t_list=DEFAULT_T_LIST,
) -> pd.DataFrame:
    """The GPD and exponential fit for a range of thresholds, one row per ``n_kept``.

    Columns: ``n_kept``, ``exclude_first``, ``threshold``, ``lam``, ``gpd_shape``,
    ``gpd_shape_se``, ``gpd_scale``, ``exp_scale`` and the level per family and T as
    ``gpd_T<T>`` / ``exp_T<T>`` (maxima space). Where the shape stops changing with the
    threshold, the threshold is high enough; that is the one to pick.
    """
    maxima = np.asarray(maxima, dtype=float)
    n_total = len(maxima)
    rows = []
    for n_kept in sorted({int(k) for k in kept_grid if 0 < int(k) <= n_total}):
        exclude_first = n_total - n_kept
        kept, lam = trim_tail(maxima, exclude_first)
        threshold = tail_threshold(maxima, exclude_first)
        row = {"n_kept": n_kept, "exclude_first": exclude_first, "threshold": threshold, "lam": lam}
        for family in TRIMMED_FAMILIES:
            try:
                params = fit_family(kept, family, threshold)
            except Exception:
                params = None
            if family == "gpd":
                shape = float(params[0]) if params is not None else float("nan")
                row.update({"gpd_shape": shape, "gpd_shape_se": gpd_shape_se(shape, n_kept),
                            "gpd_scale": float(params[-1]) if params is not None else float("nan")})
            else:
                row["exp_scale"] = float(params[-1]) if params is not None else float("nan")
            for T in t_list:
                row[f"{family}_T{T}"] = (return_level(params, family, T, lam)
                                         if params is not None else float("nan"))
        rows.append(row)
    return pd.DataFrame(rows)


def diagnose(maxima: np.ndarray, exclude_first: int = 0) -> dict:
    """Whether a tail is fit to read at all. Advisory: it flags, it does not choose.

    ``flag`` is ``insufficient`` (too few blocks), ``degenerate`` (the head is capped,
    so the extremes carry no signal), ``heavy`` (GPD shape > 0.5, a tail far heavier
    than a groundwater head should have) or empty.
    """
    maxima = np.asarray(maxima, dtype=float)
    n_blocks = len(maxima)
    spread = float(np.ptp(maxima)) if n_blocks else 0.0
    out = {"n_blocks": n_blocks, "spread_m": spread, "flag": "", "reason": ""}
    if n_blocks < MIN_BLOCKS:
        out.update(flag="insufficient", reason=f"only {n_blocks} blocks (need {MIN_BLOCKS})")
    elif spread < MIN_SPREAD:
        out.update(flag="degenerate", reason=(
            f"spread {spread:.2e} m across the pooled extremes is below {MIN_SPREAD} m; the model "
            "is capping the head against its dmin/dmax bound, so the extremes carry no signal"))
    else:
        try:
            kept, _ = trim_tail(maxima, exclude_first)
            shape = float(fit_family(kept, "gpd", tail_threshold(maxima, exclude_first))[0])
            if shape > 0.5:
                out.update(flag="heavy", reason=f"GPD shape {shape:.2f} > 0.5: a very heavy tail; check the plot")
        except Exception as exc:  # noqa: BLE001
            out.update(flag="fit_failed", reason=f"{type(exc).__name__}: {exc}")
    return out


def bootstrap_params(
    maxima: np.ndarray,
    family: str,
    replicates: int = BOOTSTRAP_REPLICATES,
    seed: int = 0,
    threshold: float | None = None,
) -> np.ndarray:
    """``family`` parameters of ``replicates`` resamples with replacement, one row each.

    For ``gpd`` / ``exp`` the lower bound stays at ``threshold``: the resampling is of
    the values above it. Resamples whose fit fails are dropped.
    """
    maxima = np.asarray(maxima, dtype=float)
    rng = np.random.default_rng(seed)
    fits = []
    for _ in range(replicates):
        sample = rng.choice(maxima, size=len(maxima), replace=True)
        try:
            fits.append(fit_family(sample, family, threshold))
        except Exception:
            continue
    width = {"gev": 3, "gpd": 3, "exp": 2}[family]
    return np.array(fits, dtype=float).reshape(-1, width)
