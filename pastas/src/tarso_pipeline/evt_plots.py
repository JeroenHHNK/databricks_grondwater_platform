"""The three extremes figures (handoff section 6.2 / 6.3).

``return_plots_fitted``  empirical points plus all three fitted curves, both
                         tails, log-T axis. The one picture in which a bad fit is
                         obvious at a glance.
``qq_plots``             empirical against theoretical quantiles per family --
                         the cleanest view of *where* a family fails.
``confidence_plots``     GEV return-level curve with a 95% bootstrap interval.
                         The interval is the honest width of the answer. Drawn by
                         ``explore_shape_and_uncertainty``, not by the extremes
                         run: the band costs hundreds of refits.

Everything is drawn on the light surface with the validated two-hue palette, and
every figure is closed explicitly: pyplot keeps them all alive otherwise, and a
20-model run draws a hundred.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from tarso_pipeline.evt import (
    FAMILIES,
    bootstrap_gev_params,
    from_maxima,
    return_level,
    trim_tail,
)

SURFACE = "#fcfcfb"
INK = "#0b0b0b"
INK_MUTED = "#52514e"
GRID = "#e6e5e2"
DRY = "#eb6834"  # categorical slot 2 - the drought tail
WET = "#2a78d6"  # categorical slot 1 - the wet tail
FAMILY_STYLE = {"gev": "-", "exp": "--", "gpd": ":"}

PLOT_T = np.logspace(np.log10(1.1), 3, 160)


def plot_periods(lam: float = 1.0) -> np.ndarray:
    """Return periods worth drawing for a given surviving fraction.

    Below ``1 / lam`` the level sits under the trim threshold and is NaN, so
    plotting it only produces all-NaN slices and an empty left edge.
    """
    return PLOT_T[PLOT_T > 1.0 / max(lam, 1e-9)] if lam < 1 else PLOT_T


def _new_axes(figsize=(9.5, 4.6), dpi=140):
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(1, 2, figsize=figsize, dpi=dpi)
    fig.patch.set_facecolor(SURFACE)
    for ax in np.atleast_1d(axes).ravel():
        ax.set_facecolor(SURFACE)
        ax.set_axisbelow(True)
        ax.grid(True, color=GRID, linewidth=0.8)
        for side in ("top", "right"):
            ax.spines[side].set_visible(False)
        for side in ("left", "bottom"):
            ax.spines[side].set_color(GRID)
            ax.spines[side].set_linewidth(0.8)
        ax.tick_params(colors=INK_MUTED, labelsize=8, length=0)
    return fig, axes


def _finish(fig, path, title):
    import matplotlib.pyplot as plt

    fig.suptitle(title, color=INK, fontsize=11, x=0.01, ha="left")
    fig.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(str(path), dpi=150, bbox_inches="tight", facecolor=SURFACE)
    plt.close(fig)
    plt.close("all")
    return str(path)


def return_period_plot(per_tail: dict, path, title: str) -> str | None:
    """Empirical points plus the three fitted curves, one panel per tail."""
    try:
        fig, axes = _new_axes()
        for ax, tail, colour in ((axes[0], "dry", DRY), (axes[1], "wet", WET)):
            if tail not in per_tail:
                ax.set_visible(False)
                continue
            data = per_tail[tail]
            points = data["points"]
            ax.scatter(
                points["return_period"],
                from_maxima(points["value"].to_numpy(), tail),
                s=9, color=colour, alpha=0.5, zorder=3, label="empirical",
            )
            for family in FAMILIES:
                info = data["parameters"].get(family)
                if info is None:
                    continue
                grid = plot_periods(info["lam"])
                levels = [
                    return_level(info["params"], family, T, info["lam"]) for T in grid
                ]
                ax.plot(
                    grid, from_maxima(np.array(levels), tail),
                    FAMILY_STYLE[family], color=colour, linewidth=1.6,
                    alpha=0.9 if family == "gev" else 0.6, label=family, zorder=4,
                )
            ax.set_xscale("log")
            # The empirical curve blows up as T->1; without clamping, autoscale
            # squeezes the signal into a fifth of the panel.
            observed = from_maxima(points["value"].to_numpy(), tail)
            margin = max(0.1, 0.15 * float(np.ptp(observed) or 1.0))
            ax.set_ylim(observed.min() - margin, observed.max() + margin)
            ax.set_xlabel("return period [years]", color=INK_MUTED, fontsize=9)
            ax.set_ylabel("head [m NAP]", color=INK_MUTED, fontsize=9)
            ax.set_title(f"{tail} tail", color=INK_MUTED, fontsize=9, loc="left")
            legend = ax.legend(frameon=False, fontsize=8, loc="best")
            for text in legend.get_texts():
                text.set_color(INK_MUTED)
        return _finish(fig, path, title)
    except Exception:
        import matplotlib.pyplot as plt

        plt.close("all")
        return None


def qq_plot(per_tail: dict, path, title: str) -> str | None:
    """Empirical against theoretical quantiles, in metres, per family."""
    from scipy.stats import expon, genextreme, genpareto

    dists = {"gev": genextreme, "exp": expon, "gpd": genpareto}
    try:
        fig, axes = _new_axes()
        for ax, tail, colour in ((axes[0], "dry", DRY), (axes[1], "wet", WET)):
            if tail not in per_tail:
                ax.set_visible(False)
                continue
            data = per_tail[tail]
            kept, _ = trim_tail(data["maxima"], 0)
            kept = np.sort(kept)
            probabilities = (np.arange(1, len(kept) + 1) - 0.5) / len(kept)
            for family in FAMILIES:
                info = data["parameters"].get(family)
                if info is None:
                    continue
                theoretical = dists[family].ppf(probabilities, *info["params"])
                ax.plot(
                    from_maxima(theoretical, tail), from_maxima(kept, tail),
                    FAMILY_STYLE[family], color=colour, linewidth=1.4,
                    alpha=0.9 if family == "gev" else 0.6, label=family,
                )
            low = min(from_maxima(kept, tail).min(), from_maxima(kept, tail).max())
            high = max(from_maxima(kept, tail).min(), from_maxima(kept, tail).max())
            ax.plot([low, high], [low, high], color=INK_MUTED, linewidth=0.8, alpha=0.5)
            ax.set_xlabel("theoretical [m NAP]", color=INK_MUTED, fontsize=9)
            ax.set_ylabel("empirical [m NAP]", color=INK_MUTED, fontsize=9)
            ax.set_title(f"{tail} tail", color=INK_MUTED, fontsize=9, loc="left")
            legend = ax.legend(frameon=False, fontsize=8, loc="best")
            for text in legend.get_texts():
                text.set_color(INK_MUTED)
        return _finish(fig, path, title)
    except Exception:
        import matplotlib.pyplot as plt

        plt.close("all")
        return None


def confidence_plot(
    per_tail: dict,
    path,
    title: str,
    replicates: int = 500,
    seed: int = 0,
    exclude_first: int = 0,
    resamples: dict | None = None,
) -> str | None:
    """GEV return-level curve with a 95% bootstrap interval.

    The interval is the point: a return level without one invites more confidence
    than 704 blocks support, especially past T=100.

    ``resamples`` maps a tail to its array of bootstrap GEV fits
    (:func:`tarso_pipeline.evt.bootstrap_gev_params`); a tail without one is
    bootstrapped here with ``replicates``.
    """
    from tarso_pipeline.uncertainty import gev_band, gev_levels

    try:
        fig, axes = _new_axes()
        for ax, tail, colour in ((axes[0], "dry", DRY), (axes[1], "wet", WET)):
            if tail not in per_tail:
                ax.set_visible(False)
                continue
            data = per_tail[tail]
            info = data["parameters"].get("gev")
            if info is None:
                ax.set_visible(False)
                continue
            kept, lam = trim_tail(data["maxima"], exclude_first)

            grid = plot_periods(lam)
            fits = (resamples or {}).get(tail)
            if fits is None:
                fits = bootstrap_gev_params(kept, replicates=replicates, seed=seed,
                                            start=info["params"])
            central = gev_levels(info["params"], grid, info["lam"])

            if len(fits):
                low, high = gev_band(fits, grid, lam)
                ax.fill_between(
                    grid, from_maxima(low, tail), from_maxima(high, tail),
                    color=colour, alpha=0.18, linewidth=0,
                    label=f"95% CI ({len(fits)} resamples)",
                )
            ax.plot(grid, from_maxima(central, tail), color=colour,
                    linewidth=1.8, label="GEV", zorder=4)

            points = data["points"]
            observed = from_maxima(points["value"].to_numpy(), tail)
            ax.scatter(points["return_period"], observed, s=9, color=INK_MUTED,
                       alpha=0.4, zorder=3, label="empirical")

            ax.set_xscale("log")
            margin = max(0.1, 0.15 * float(np.ptp(observed) or 1.0))
            ax.set_ylim(observed.min() - margin, observed.max() + margin)
            ax.set_xlabel("return period [years]", color=INK_MUTED, fontsize=9)
            ax.set_ylabel("head [m NAP]", color=INK_MUTED, fontsize=9)
            ax.set_title(f"{tail} tail", color=INK_MUTED, fontsize=9, loc="left")
            legend = ax.legend(frameon=False, fontsize=8, loc="best")
            for text in legend.get_texts():
                text.set_color(INK_MUTED)
        return _finish(fig, path, title)
    except Exception:
        import matplotlib.pyplot as plt

        plt.close("all")
        return None
