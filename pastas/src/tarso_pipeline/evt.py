"""Extreme-value fits, return levels and automatic family selection (section 5).

Both tails are fitted as **maxima**: a drought minimum is a maximum of ``-head``.
Three families are fitted and all three reported, but only two are ever
recommended:

``gev``  block maxima are what the GEV belongs on. Gumbel/Weibull/Frechet are not
         rivals -- they are the GEV indexed by its shape.
``exp``  one-parameter exponential tail; the parsimonious read when the shape is
         indistinguishable from zero.
``gpd``  **never recommended.** A generalised Pareto belongs on threshold
         exceedances, not block maxima, and here its location is pinned to the
         sample minimum -- not a construction that supports a return level.
         Reported for comparison only.

Shape convention follows scipy's ``genextreme``: ``c > 0`` is a bounded (Weibull)
tail, ``c < 0`` unbounded (Frechet).

Trimming
--------
``exclude_first`` drops the N least extreme values before fitting. This changes
*what the distribution is*: what remains is conditional on clearing a threshold,
not an annual maximum. If a fraction ``lam = n_kept / n_total`` survives, a
T-year event is the ``1 / (lam * T)`` quantile, **not** the ``1 / T`` one.
Without that correction levels drift steadily more extreme as more is trimmed.

A consequence worth stating: when ``lam * T <= 1`` the T-year level sits below
the trim threshold and cannot be recovered from the trimmed sample at all. It is
returned as NaN rather than as a wrong number.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

FAMILIES = ("exp", "gpd", "gev")
RECOMMENDABLE = ("gev", "exp")

DEFAULT_T_LIST = (2, 5, 10, 25, 50, 100, 250, 500, 1000)

# Fewer blocks than this and the shape is not resolvable; flag for review.
MIN_BLOCKS = 100

# Under a millimetre of variation across a yearly extreme is not signal. This is
# the Tarso failure mode: a model whose dmax ceiling caps the head produces 704
# identical annual maxima, which a GEV fits perfectly cleanly and meaninglessly.
MIN_SPREAD = 1e-3

BOOTSTRAP_REPLICATES = 200

# A T=100 level this far outside the span of the pooled extremes is physically
# suspect even when the statistics are clean.
IMPLAUSIBLE_MARGIN = 2.0


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


def fit_family(maxima: np.ndarray, family: str) -> tuple[float, ...]:
    """Fit one family; returns scipy's parameter tuple."""
    from scipy.stats import expon, genextreme, genpareto

    maxima = np.asarray(maxima, dtype=float)
    if family == "gev":
        return genextreme.fit(maxima)
    if family == "exp":
        return expon.fit(maxima)
    if family == "gpd":
        # Location pinned to the sample minimum, per the original. Reported for
        # comparison only -- never recommended.
        return genpareto.fit(maxima, floc=float(np.min(maxima)))
    raise ValueError(f"Unknown family {family!r}; use one of {FAMILIES}")


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


def bootstrap_gev_params(
    maxima: np.ndarray,
    replicates: int = BOOTSTRAP_REPLICATES,
    seed: int = 0,
    start: tuple[float, float, float] | None = None,
) -> np.ndarray:
    """GEV ``(shape, loc, scale)`` of ``replicates`` resamples with replacement.

    One array of resampled fits serves both the shape SE and the return-level
    band, so the two never need separate bootstraps. Each fit is warm-started from
    ``start`` (the full-sample fit; computed when not given): a resample sits close
    to it, so the optimiser converges in a few steps instead of searching from
    scipy's default guess. Resamples whose fit fails are dropped.
    """
    from scipy.stats import genextreme

    maxima = np.asarray(maxima, dtype=float)
    if start is None:
        start = genextreme.fit(maxima)
    c0, loc0, scale0 = (float(v) for v in start)
    rng = np.random.default_rng(seed)
    fits = []
    for _ in range(replicates):
        sample = rng.choice(maxima, size=len(maxima), replace=True)
        try:
            fits.append(genextreme.fit(sample, c0, loc=loc0, scale=scale0))
        except Exception:
            continue
    return np.array(fits, dtype=float).reshape(-1, 3)


def bootstrap_shape_se(
    maxima: np.ndarray,
    replicates: int = BOOTSTRAP_REPLICATES,
    seed: int = 0,
    start: tuple[float, float, float] | None = None,
) -> float:
    """Standard error of the GEV shape, by resampling with replacement.

    With a single 44-year record the shape SE is ~0.13 -- as large as the spread
    of shapes across a whole batch, which is why a human had to look at the plot.
    Pooling the ensemble to 704 blocks drops it to ~0.025, and that is what makes
    the family choice automatable.

    Costs ``replicates`` GEV fits. The extremes run uses :func:`hessian_shape_se`
    instead; this one lives on in ``explore_shape_and_uncertainty`` as its check.
    """
    fits = bootstrap_gev_params(maxima, replicates=replicates, seed=seed, start=start)
    return float(np.std(fits[:, 0])) if len(fits) > 1 else float("nan")


# Above this scipy shape (a bounded tail with xi < -0.5) maximum likelihood is
# non-regular (Smith 1985) and the information matrix says nothing about the
# sampling spread, so the Hessian SE is not used there.
HESSIAN_MAX_SHAPE = 0.5


def hessian_shape_se(
    maxima: np.ndarray,
    params: tuple[float, float, float] | None = None,
) -> float:
    """Standard error of the GEV shape from the curvature of the likelihood.

    The observed information -- the Hessian of the negative log-likelihood at the
    maximum-likelihood fit, by central differences -- inverted; the shape's
    diagonal element. One fit and a dozen likelihood evaluations instead of the
    bootstrap's hundreds of fits; on a pooled sample of hundreds of blocks the
    two agree closely.

    NaN when it cannot be trusted: the shape lies in the non-regular region
    (``> HESSIAN_MAX_SHAPE``), a step lands outside the GEV support, or the
    Hessian is not positive definite. :func:`select_family` then falls back to
    the bootstrap.
    """
    from scipy.stats import genextreme

    x = np.asarray(maxima, dtype=float)
    theta = np.asarray(params if params is not None else genextreme.fit(x), dtype=float)
    if not np.all(np.isfinite(theta)) or theta[2] <= 0 or theta[0] > HESSIAN_MAX_SHAPE:
        return float("nan")

    def nll(t: np.ndarray) -> float:
        return float(genextreme.nnlf((t[0], t[1], t[2]), x)) if t[2] > 0 else np.inf

    steps = 1e-4 * np.maximum(np.abs(theta), theta[2])
    eye = np.eye(3)
    f0 = nll(theta)
    hessian = np.empty((3, 3))
    for i in range(3):
        hi = steps[i] * eye[i]
        hessian[i, i] = (nll(theta + hi) - 2 * f0 + nll(theta - hi)) / steps[i] ** 2
        for j in range(i + 1, 3):
            hj = steps[j] * eye[j]
            hessian[i, j] = hessian[j, i] = (
                nll(theta + hi + hj) - nll(theta + hi - hj)
                - nll(theta - hi + hj) + nll(theta - hi - hj)
            ) / (4 * steps[i] * steps[j])
    if not np.all(np.isfinite(hessian)):
        return float("nan")
    try:
        variance = float(np.linalg.inv(hessian)[0, 0])
    except np.linalg.LinAlgError:
        return float("nan")
    return float(np.sqrt(variance)) if variance > 0 else float("nan")


SE_METHODS = ("hessian", "bootstrap")


@dataclass(frozen=True)
class Selection:
    """The recommended family for one tail, and why."""

    family: str | None
    verdict: str
    shape: float | None
    shape_se: float | None
    n_blocks: int
    review_flag: bool
    reason: str
    params: dict = field(default_factory=dict)
    shape_se_method: str | None = None

    def as_row(self) -> dict:
        return {
            "chosen_family": self.family,
            "verdict": self.verdict,
            "shape": self.shape,
            "shape_se": self.shape_se,
            "shape_se_method": self.shape_se_method,
            "n_blocks": self.n_blocks,
            "review_flag": self.review_flag,
            "reason": self.reason,
        }


def select_family(
    maxima: np.ndarray,
    exclude_first: int = 0,
    replicates: int = BOOTSTRAP_REPLICATES,
    seed: int = 0,
    spread_reference: np.ndarray | None = None,
    se_method: str = "hessian",
    shape_se: float | None = None,
) -> Selection:
    """Recommend a family for one tail. Everything here is advisory.

    Every guard rail sets ``review_flag`` rather than dropping a series, and a
    hand-set family always wins. The shape, its SE, the verdict and the reason
    are all recorded, which is what makes an automatic choice auditable.

    The SE that separates ``exp`` from ``gev`` comes from ``se_method``:
    ``hessian`` (the default; one calculation) or ``bootstrap`` (``replicates``
    refits). When the Hessian is unusable the bootstrap is used, and
    ``shape_se_method`` says so. Passing ``shape_se`` skips both and applies that
    SE: how ``explore_shape_and_uncertainty`` asks what the bootstrap would pick.
    """
    if se_method not in SE_METHODS:
        raise ValueError(f"se_method must be one of {SE_METHODS}, got {se_method!r}")
    maxima = np.asarray(maxima, dtype=float)
    n_blocks = len(maxima)
    reference = maxima if spread_reference is None else np.asarray(spread_reference, float)

    if n_blocks < MIN_BLOCKS:
        return Selection(
            family=None, verdict="insufficient", shape=None, shape_se=None,
            n_blocks=n_blocks, review_flag=True,
            reason=f"only {n_blocks} blocks (need {MIN_BLOCKS})",
        )

    spread = float(np.ptp(reference)) if len(reference) else 0.0
    if spread < MIN_SPREAD:
        return Selection(
            family=None, verdict="degenerate", shape=None, shape_se=None,
            n_blocks=n_blocks, review_flag=True,
            reason=(
                f"spread {spread:.2e} m across the pooled extremes is below "
                f"{MIN_SPREAD} m; the model is capping the head against its "
                "dmin/dmax bound, so the extremes carry no signal"
            ),
        )

    kept, lam = trim_tail(maxima, exclude_first)
    try:
        shape, loc, scale = fit_family(kept, "gev")
    except Exception as exc:
        return Selection(
            family=None, verdict="fit_failed", shape=None, shape_se=None,
            n_blocks=n_blocks, review_flag=True,
            reason=f"GEV fit failed: {type(exc).__name__}: {exc}",
        )

    if shape_se is not None:
        se, method = float(shape_se), se_method
    else:
        se, method = float("nan"), se_method
        if se_method == "hessian":
            se = hessian_shape_se(kept, (shape, loc, scale))
            if not np.isfinite(se):
                method = "bootstrap (hessian unusable)"
        if not np.isfinite(se):
            se = bootstrap_shape_se(kept, replicates=replicates, seed=seed,
                                    start=(shape, loc, scale))
    params = {"shape": float(shape), "loc": float(loc), "scale": float(scale), "lam": lam}

    if shape < -0.5:
        return Selection(
            family="gev", verdict="heavy_tail_unstable", shape=float(shape),
            shape_se=se, n_blocks=n_blocks, review_flag=True,
            reason=f"shape {shape:.3f} < -0.5; GEV maximum likelihood is irregular there",
            params=params, shape_se_method=method,
        )

    if np.isfinite(se) and abs(shape) < 2 * se:
        return Selection(
            family="exp", verdict="gumbel", shape=float(shape), shape_se=se,
            n_blocks=n_blocks, review_flag=False,
            reason=(
                f"|shape| {abs(shape):.3f} < 2*SE {2 * se:.3f}; the shape is not "
                "resolved, so the exponential form is the honest read"
            ),
            params=params, shape_se_method=method,
        )

    if shape > 0:
        return Selection(
            family="gev", verdict="weibull", shape=float(shape), shape_se=se,
            n_blocks=n_blocks, review_flag=False,
            reason=f"shape {shape:.3f} > 0: bounded tail",
            params=params, shape_se_method=method,
        )

    return Selection(
        family="gev", verdict="frechet", shape=float(shape), shape_se=se,
        n_blocks=n_blocks, review_flag=True,
        reason=(
            f"shape {shape:.3f} < 0: unbounded tail. Statistically clean, but an "
            "unbounded tail on a groundwater head deserves a second look"
        ),
        params=params, shape_se_method=method,
    )


def fit_all_families(
    maxima: np.ndarray,
    exclude_first: int = 0,
    t_list=DEFAULT_T_LIST,
) -> tuple[dict, pd.DataFrame]:
    """Fit all three families and tabulate their return levels (maxima space).

    Returns ``(parameters_by_family, levels)`` where ``levels`` has one row per T
    and one column per family.
    """
    kept, lam = trim_tail(maxima, exclude_first)
    parameters, columns = {}, {}
    for family in FAMILIES:
        try:
            params = fit_family(kept, family)
        except Exception:
            parameters[family] = None
            columns[family] = [float("nan")] * len(t_list)
            continue
        parameters[family] = {
            "params": tuple(float(p) for p in params),
            "lam": lam,
            "n_total": len(maxima),
            "n_fitted": len(kept),
            "exceedance_rate": lam,
        }
        columns[family] = [return_level(params, family, T, lam) for T in t_list]

    levels = pd.DataFrame(columns, index=pd.Index(t_list, name="return_period"))
    return parameters, levels
