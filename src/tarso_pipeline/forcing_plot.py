"""Annual summaries and the check chart for daily forcing series.

Pure pandas and matplotlib: no Spark, no dbutils. The Databricks notebook and the
local runner (``scripts/check_knmi.py``) both import from here, so what you see in
the terminal is what the job produces -- there is no second copy to drift.

The palette is categorical slots 1 and 2 of the house scale, validated against the
light surface for colour-vision deficiency (CVD dE 24.7, normal-vision dE 33.6,
both at least 3:1 contrast).
"""

from __future__ import annotations

import calendar

import matplotlib
import pandas as pd

SURFACE = "#fcfcfb"
INK = "#0b0b0b"
INK_MUTED = "#52514e"
GRID = "#e6e5e2"
PRECIP = "#2a78d6"
EVAP = "#eb6834"


def annual_totals(daily: pd.DataFrame) -> pd.DataFrame:
    """Year -> cumulative precipitation and Makkink evaporation in mm.

    ``daily`` needs a datetime ``date`` column plus ``precipitation_mm`` and
    ``makkink_mm``. The returned frame is indexed by year and also carries
    ``days``, ``last_date`` and ``complete`` -- a year is complete only when every
    calendar day is present, which is what makes it comparable to its neighbours.
    """
    frame = daily.copy()
    frame["date"] = pd.to_datetime(frame["date"])
    grouped = frame.assign(year=frame["date"].dt.year).groupby("year")

    out = grouped[["precipitation_mm", "makkink_mm"]].sum().round(1)
    out["days"] = grouped["date"].nunique()
    out["last_date"] = grouped["date"].max()
    out["complete"] = out["days"] == [
        366 if calendar.isleap(year) else 365 for year in out.index
    ]
    return out


def plot_annual_totals(totals: pd.DataFrame, station: int):
    """Grouped bars of annual precipitation vs Makkink evaporation.

    Both series are millimetres, so they share one axis and can be read against
    each other directly: precipitation above evaporation is a recharge surplus for
    that year. Incomplete years are marked ``*`` and footnoted rather than being
    silently compared against full ones.
    """
    import matplotlib.pyplot as plt

    years = totals.index.tolist()
    x = range(len(years))
    width = 0.30
    gap = 0.02  # surface gap between the paired bars, never a border

    fig, ax = plt.subplots(figsize=(9.5, 4.6), dpi=140)
    fig.patch.set_facecolor(SURFACE)
    ax.set_facecolor(SURFACE)

    ax.set_axisbelow(True)
    ax.yaxis.grid(True, color=GRID, linewidth=0.8, linestyle="-")
    ax.xaxis.grid(False)
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_color(GRID)
    ax.spines["bottom"].set_linewidth(0.8)
    ax.tick_params(axis="both", length=0, colors=INK_MUTED, labelsize=9)

    ax.bar(
        [i - width / 2 - gap for i in x], totals["precipitation_mm"],
        width=width, color=PRECIP, label="Precipitation", zorder=3,
    )
    ax.bar(
        [i + width / 2 + gap for i in x], totals["makkink_mm"],
        width=width, color=EVAP, label="Makkink evaporation", zorder=3,
    )

    ax.set_xticks(list(x))
    ax.set_xticklabels(
        [f"{y}*" if not totals.loc[y, "complete"] else str(y) for y in years]
    )
    ax.set_ylabel("mm per year", color=INK_MUTED, fontsize=9)
    ax.set_title(
        f"Annual precipitation and Makkink evaporation - KNMI station {station}",
        color=INK, fontsize=12, pad=26, loc="left",
    )

    legend = ax.legend(
        loc="upper left", bbox_to_anchor=(0, 1.08), ncols=2,
        frameon=False, fontsize=9, handlelength=0.9, handleheight=0.9,
    )
    for text in legend.get_texts():
        text.set_color(INK_MUTED)

    incomplete = totals.index[~totals["complete"]].tolist()
    if incomplete:
        fig.text(
            0.005, -0.02,
            ", ".join(
                f"{y}* partial: through {totals.loc[y, 'last_date'].date()}"
                for y in incomplete
            ),
            fontsize=8, color=INK_MUTED,
        )

    ax.margins(x=0.04)
    fig.tight_layout()
    return fig


def use_headless_backend() -> None:
    """Select Agg, for a cluster or a terminal with no display."""
    matplotlib.use("Agg")
