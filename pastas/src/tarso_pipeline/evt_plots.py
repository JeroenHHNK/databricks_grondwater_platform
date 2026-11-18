"""The extremes figures (handoff section 6.2 / 6.3).

``return_plots_fitted``  the empirical points of all pooled years, the GPD and the
                         exponential fitted above the threshold, and the GEV on all
                         years for comparison; both tails, log-T axis. Up to T=100 the
                         empirical level is the one used, so that range is shaded.
``threshold_plots``      the GPD shape and the T=1000 level for a range of thresholds:
                         where they stop changing, the threshold is high enough.
``qq_plots``             empirical against fitted quantiles per family.
``confidence_plots``     GPD and exponential with a 95% bootstrap band. Drawn by
                         ``explore_shape_and_uncertainty``, not by the extremes run.

Everything is drawn on the light surface with the validated two-hue palette, and
every figure is closed explicitly: pyplot keeps them all alive otherwise, and a
20-model run draws a hundred.
"""

from __future__ import annotations

import numpy as np

from tarso_pipeline.evt import (
    EMPIRICAL_MAX_T,
    INDICATIVE_MIN_T,
    TRIMMED_FAMILIES,
    bootstrap_params,
    from_maxima,
    return_level,
    tail_threshold,
    trim_tail,
)

SURFACE = "#fcfcfb"
INK = "#0b0b0b"
INK_MUTED = "#52514e"
GRID = "#e6e5e2"
SHADE = "#efeeeb"
DRY = "#eb6834"  # categorical slot 2 - the drought tail
WET = "#2a78d6"  # categorical slot 1 - the wet tail
FAMILY_STYLE = {"gpd": "-", "exp": "--", "gev": ":"}
FAMILY_LABEL = {"gpd": "GPD (boven drempel)", "exp": "exponentieel (boven drempel)",
                "gev": "GEV (alle jaren, vergelijking)"}

PLOT_T = np.logspace(np.log10(1.1), 3, 160)


def plot_periods(lam: float = 1.0) -> np.ndarray:
    """Return periods worth drawing for a given surviving fraction.

    Below ``1 / lam`` the level sits under the trim threshold and is NaN, so
    plotting it only produces all-NaN slices and an empty left edge.
    """
    return PLOT_T[PLOT_T > 1.0 / max(lam, 1e-9)] if lam < 1 else PLOT_T


def _new_axes(nrows=1, figsize=(9.5, 4.6), dpi=140):
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(nrows, 2, figsize=figsize, dpi=dpi, squeeze=False)
    fig.patch.set_facecolor(SURFACE)
    for ax in axes.ravel():
        ax.set_facecolor(SURFACE)
        ax.set_axisbelow(True)
        ax.grid(True, color=GRID, linewidth=0.8)
        for side in ("top", "right"):
            ax.spines[side].set_visible(False)
        for side in ("left", "bottom"):
            ax.spines[side].set_color(GRID)
            ax.spines[side].set_linewidth(0.8)
        ax.tick_params(colors=INK_MUTED, labelsize=8, length=0)
    return fig, (axes[0] if nrows == 1 else axes)


def _finish(fig, path, title):
    import matplotlib.pyplot as plt

    fig.suptitle(title, color=INK, fontsize=11, x=0.01, ha="left")
    fig.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(str(path), dpi=150, bbox_inches="tight", facecolor=SURFACE)
    plt.close(fig)
    plt.close("all")
    return str(path)


def _legend(ax):
    legend = ax.legend(frameon=False, fontsize=7.5, loc="best")
    for text in legend.get_texts():
        text.set_color(INK_MUTED)


def _guard(draw):
    """Run a figure function; on any failure close everything and return None."""
    def wrapped(*args, **kwargs):
        try:
            return draw(*args, **kwargs)
        except Exception:
            import matplotlib.pyplot as plt

            plt.close("all")
            return None
    wrapped.__name__, wrapped.__doc__ = draw.__name__, draw.__doc__
    return wrapped


def _return_axes(ax, tail, observed):
    ax.set_xscale("log")
    ax.set_xlim(1.0, 1100)
    # The empirical curve blows up as T->1; without clamping, autoscale squeezes
    # the signal into a fifth of the panel.
    margin = max(0.1, 0.15 * float(np.ptp(observed) or 1.0))
    ax.set_ylim(observed.min() - margin, observed.max() + margin)
    ax.axvspan(1.0, EMPIRICAL_MAX_T, color=SHADE, zorder=0, linewidth=0)
    ax.text(1.15, 0.02, f"T ≤ {EMPIRICAL_MAX_T}: empirisch gebruikt", transform=ax.get_xaxis_transform(),
            fontsize=7, color=INK_MUTED)
    ax.axvline(INDICATIVE_MIN_T, color=GRID, linewidth=1)
    ax.text(INDICATIVE_MIN_T, 0.02, "indicatief ", transform=ax.get_xaxis_transform(), fontsize=7,
            color=INK_MUTED, ha="right")
    ax.set_xlabel("return period [years]", color=INK_MUTED, fontsize=9)
    ax.set_ylabel("head [m NAP]", color=INK_MUTED, fontsize=9)
    ax.set_title(f"{tail} tail", color=INK_MUTED, fontsize=9, loc="left")


@_guard
def return_period_plot(per_tail: dict, path, title: str) -> str | None:
    """Empirical points, GPD and exponential above the threshold, GEV on all years."""
    fig, axes = _new_axes()
    for ax, tail, colour in ((axes[0], "dry", DRY), (axes[1], "wet", WET)):
        if tail not in per_tail:
            ax.set_visible(False)
            continue
        data = per_tail[tail]
        points = data["points"]
        observed = from_maxima(points["value"].to_numpy(), tail)
        ax.scatter(points["return_period"], observed, s=8, color=INK_MUTED, alpha=0.45, zorder=3,
                   label=f"empirisch ({len(points)} jaren)")
        for family in ("gpd", "exp", "gev"):
            info = data["parameters"].get(family)
            if info is None:
                continue
            grid = plot_periods(info["lam"])
            levels = [return_level(info["params"], family, T, info["lam"]) for T in grid]
            ax.plot(grid, from_maxima(np.array(levels), tail), FAMILY_STYLE[family],
                    color=INK_MUTED if family == "gev" else colour,
                    linewidth=1.2 if family == "gev" else 1.8, label=FAMILY_LABEL[family], zorder=4)
        ax.axhline(data["threshold"], color=colour, linewidth=0.8, alpha=0.5)
        ax.text(1.15, data["threshold"], f" drempel ({data['n_kept_default']} jaren erboven)",
                fontsize=7, color=INK_MUTED, va="bottom")
        _return_axes(ax, tail, observed)
        _legend(ax)
    return _finish(fig, path, title)


@_guard
def threshold_plot(per_tail: dict, path, title: str) -> str | None:
    """GPD shape (top) and the T=1000 level of GPD and exponential (bottom) per threshold."""
    fig, axes = _new_axes(nrows=2, figsize=(9.5, 7.0))
    for col, tail, colour in ((0, "dry", DRY), (1, "wet", WET)):
        top, bottom = axes[0][col], axes[1][col]
        if tail not in per_tail:
            top.set_visible(False)
            bottom.set_visible(False)
            continue
        data = per_tail[tail]
        scan = data["scan"]
        kept = scan["n_kept"].to_numpy()
        shape = scan["gpd_shape"].to_numpy()
        half = 1.96 * scan["gpd_shape_se"].to_numpy()
        top.fill_between(kept, shape - half, shape + half, color=colour, alpha=0.15, linewidth=0,
                         label="95% (asymptotisch)")
        top.plot(kept, shape, "o-", color=colour, markersize=4, linewidth=1.5, label="GPD-vorm")
        top.axhline(0, color=INK_MUTED, linewidth=0.8, linestyle="--", label="0 = exponentieel")
        top.set_ylabel("GPD shape", color=INK_MUTED, fontsize=9)
        top.set_title(f"{tail} tail: stabiel = drempel hoog genoeg", color=INK_MUTED, fontsize=9, loc="left")

        for family in TRIMMED_FAMILIES:
            column = f"{family}_T{INDICATIVE_MIN_T}"
            if column in scan:
                bottom.plot(kept, from_maxima(scan[column].to_numpy(), tail), FAMILY_STYLE[family] + "o",
                            color=colour, markersize=3.5, linewidth=1.5,
                            label=f"{FAMILY_LABEL[family].split(' (')[0]} T={INDICATIVE_MIN_T}")
        column = f"gpd_T{EMPIRICAL_MAX_T}"
        if column in scan:
            bottom.plot(kept, from_maxima(scan[column].to_numpy(), tail), "-", color=INK_MUTED,
                        linewidth=1, alpha=0.7, label=f"GPD T={EMPIRICAL_MAX_T}")
        bottom.set_xlabel("aantal jaren boven de drempel (n_kept)", color=INK_MUTED, fontsize=9)
        bottom.set_ylabel("head [m NAP]", color=INK_MUTED, fontsize=9)
        for ax in (top, bottom):
            ax.axvline(data["n_kept_default"], color=GRID, linewidth=1.5)
            _legend(ax)
    return _finish(fig, path, title)


@_guard
def qq_plot(per_tail: dict, path, title: str) -> str | None:
    """Empirical against fitted quantiles, in metres: GPD/exp on the values above the
    threshold, GEV on all years."""
    from scipy.stats import expon, genextreme, genpareto

    dists = {"gev": genextreme, "exp": expon, "gpd": genpareto}
    fig, axes = _new_axes()
    for ax, tail, colour in ((axes[0], "dry", DRY), (axes[1], "wet", WET)):
        if tail not in per_tail:
            ax.set_visible(False)
            continue
        data = per_tail[tail]
        everything = np.sort(data["maxima"])
        kept, _ = trim_tail(data["maxima"], data["exclude_first"])
        for family in ("gpd", "exp", "gev"):
            info = data["parameters"].get(family)
            if info is None:
                continue
            sample = everything if family == "gev" else np.sort(kept)
            probabilities = (np.arange(1, len(sample) + 1) - 0.5) / len(sample)
            theoretical = dists[family].ppf(probabilities, *info["params"])
            ax.plot(from_maxima(theoretical, tail), from_maxima(sample, tail), FAMILY_STYLE[family],
                    color=INK_MUTED if family == "gev" else colour, linewidth=1.4, label=FAMILY_LABEL[family])
        low, high = sorted((float(from_maxima(everything, tail).min()), float(from_maxima(everything, tail).max())))
        ax.plot([low, high], [low, high], color=INK_MUTED, linewidth=0.8, alpha=0.5)
        ax.set_xlabel("fitted [m NAP]", color=INK_MUTED, fontsize=9)
        ax.set_ylabel("empirical [m NAP]", color=INK_MUTED, fontsize=9)
        ax.set_title(f"{tail} tail", color=INK_MUTED, fontsize=9, loc="left")
        _legend(ax)
    return _finish(fig, path, title)


@_guard
def confidence_plot(
    per_tail: dict,
    path,
    title: str,
    replicates: int = 500,
    seed: int = 0,
    resamples: dict | None = None,
) -> str | None:
    """GPD and exponential above the threshold, each with a 95% bootstrap band.

    ``resamples`` maps a tail to ``{family: array of bootstrap parameters}``
    (:func:`tarso_pipeline.evt.bootstrap_params`); what is missing is bootstrapped
    here with ``replicates``.
    """
    from tarso_pipeline.uncertainty import band, family_levels

    fig, axes = _new_axes()
    for ax, tail, colour in ((axes[0], "dry", DRY), (axes[1], "wet", WET)):
        if tail not in per_tail:
            ax.set_visible(False)
            continue
        data = per_tail[tail]
        kept, lam = trim_tail(data["maxima"], data["exclude_first"])
        threshold = tail_threshold(data["maxima"], data["exclude_first"])
        grid = plot_periods(lam)
        for family in TRIMMED_FAMILIES:
            info = data["parameters"].get(family)
            if info is None:
                continue
            fits = ((resamples or {}).get(tail) or {}).get(family)
            if fits is None:
                fits = bootstrap_params(kept, family, replicates=replicates, seed=seed, threshold=threshold)
            if len(fits):
                low, high = band(fits, family, grid, lam)
                ax.fill_between(grid, from_maxima(low, tail), from_maxima(high, tail), color=colour,
                                alpha=0.18 if family == "gpd" else 0.08, linewidth=0)
            central = family_levels(info["params"], family, grid, info["lam"])
            ax.plot(grid, from_maxima(central, tail), FAMILY_STYLE[family], color=colour, linewidth=1.8,
                    label=f"{FAMILY_LABEL[family]} + 95% ({len(fits)} resamples)", zorder=4)
        points = data["points"]
        observed = from_maxima(points["value"].to_numpy(), tail)
        ax.scatter(points["return_period"], observed, s=8, color=INK_MUTED, alpha=0.4, zorder=3,
                   label="empirisch")
        _return_axes(ax, tail, observed)
        _legend(ax)
    return _finish(fig, path, title)
