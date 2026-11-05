"""Assembling the extremes stage's tables (handoff section 6).

Four mandatory artefacts plus two recommended ones:

``csv/<series>_return_periods.csv``   the deliverable: T x tail x family levels
``evt_selection.csv``                 which family was chosen per tail, and why
``<run>_extremes_summary.csv``        the run's report card, one row per model
``pooled_extremes/<series>_{dry,wet}.csv``  every pooled extreme, re-fittable
``tail_parameters.csv``               shape/loc/scale per family per tail
``used_extremes/used_extremes_<series>.csv``  which extremes the fit actually saw

All levels are reported in **head units (metres NAP)**, not the maxima space the
fitting happens in: the dry tail is flipped back on the way out.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from tarso_pipeline.evt import (
    DEFAULT_T_LIST,
    FAMILIES,
    as_maxima,
    empirical_points,
    fit_all_families,
    from_maxima,
    select_family,
    trim_tail,
)

TAILS = ("dry", "wet")
TAIL_PREFIX = {"dry": "dr", "wet": "wet"}
VALUE_COLUMN = {"dry": "head_min", "wet": "head_max"}


def empirical_level(points: pd.DataFrame, return_period: float) -> float:
    """Empirical level at T, interpolated on the plotting positions.

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


def tail_tables(
    values: np.ndarray,
    tail: str,
    exclude_first: int = 0,
    t_list=DEFAULT_T_LIST,
    replicates: int = 200,
    seed: int = 0,
) -> dict:
    """Everything for one tail: levels, parameters, selection, empirical points."""
    maxima = as_maxima(values, tail)
    parameters, levels_maxima = fit_all_families(maxima, exclude_first, t_list)
    points = empirical_points(maxima, exclude_first)

    # Back into head units; for the dry tail that flips the sign.
    levels = pd.DataFrame(index=levels_maxima.index)
    for family in FAMILIES:
        levels[family] = from_maxima(levels_maxima[family].to_numpy(), tail)
    levels["emp"] = from_maxima(
        np.array([empirical_level(points, T) for T in t_list]), tail
    )

    selection = select_family(
        maxima, exclude_first=exclude_first, replicates=replicates, seed=seed,
        spread_reference=maxima,
    )
    return {
        "levels": levels,
        "parameters": parameters,
        "selection": selection,
        "points": points,
        "maxima": maxima,
    }


def return_period_table(per_tail: dict, t_list=DEFAULT_T_LIST) -> pd.DataFrame:
    """``T x {dr_,wet_} x {exp,gpd,gev,emp}`` -- the deliverable."""
    out = pd.DataFrame(index=pd.Index(t_list, name="return_period"))
    for tail in TAILS:
        if tail not in per_tail:
            continue
        prefix = TAIL_PREFIX[tail]
        levels = per_tail[tail]["levels"]
        for family in (*FAMILIES, "emp"):
            out[f"{prefix}_{family}"] = levels[family]
    return out


def selection_rows(meetreeks_id: str, series_name: str, run_id: str, per_tail: dict) -> list[dict]:
    """One row per tail for ``evt_selection.csv``."""
    rows = []
    for tail in TAILS:
        if tail not in per_tail:
            continue
        row = {
            "run_id": run_id,
            "meetreeks_id": meetreeks_id,
            "series_name": series_name,
            "model_type": "tarso",
            "tail": tail,
        }
        row.update(per_tail[tail]["selection"].as_row())
        rows.append(row)
    return rows


def tail_parameter_rows(
    meetreeks_id: str, series_name: str, run_id: str, per_tail: dict
) -> list[dict]:
    """Three rows per tail: the fitted parameters the level table throws away."""
    rows = []
    for tail in TAILS:
        if tail not in per_tail:
            continue
        for family, info in per_tail[tail]["parameters"].items():
            base = {
                "run_id": run_id,
                "meetreeks_id": meetreeks_id,
                "series_name": series_name,
                "tail": tail,
                "family": family,
            }
            if info is None:
                base.update(
                    {"shape": None, "loc": None, "scale": None, "n_total": None,
                     "n_fitted": None, "exceedance_rate": None, "fit_ok": False}
                )
            else:
                params = info["params"]
                # exp has (loc, scale); gev and gpd have (shape, loc, scale).
                shape = params[0] if len(params) == 3 else None
                loc, scale = (params[-2], params[-1])
                base.update(
                    {
                        "shape": shape, "loc": loc, "scale": scale,
                        "n_total": info["n_total"], "n_fitted": info["n_fitted"],
                        "exceedance_rate": info["exceedance_rate"], "fit_ok": True,
                    }
                )
            rows.append(base)
    return rows


def used_extremes_table(pooled: dict, exclude_first: int = 0) -> pd.DataFrame:
    """Every extreme with member, hydrological year, date, rank and ``used``.

    Rank 1 is the most extreme. ``used`` is False for the values trimming
    dropped, which is the whole point of the table: it audits what the fit saw.
    """
    frames = []
    for tail in TAILS:
        frame = pooled.get(tail)
        if frame is None or frame.empty:
            continue
        column = VALUE_COLUMN[tail]
        work = frame.reset_index().rename(columns={column: "value"})
        work["tail"] = tail
        # Rank on the maxima, so 1 is the most extreme in the tail's own sense.
        work["rank"] = (
            pd.Series(as_maxima(work["value"].to_numpy(), tail))
            .rank(ascending=False, method="first")
            .astype(int)
        )
        n_total = len(work)
        work["used"] = work["rank"] <= (n_total - exclude_first)
        frames.append(work)
    if not frames:
        return pd.DataFrame()
    out = pd.concat(frames, ignore_index=True)
    return out.sort_values(["tail", "rank"]).reset_index(drop=True)


def summary_row(
    meetreeks_id: str,
    series_name: str,
    run_id: str,
    pooled: dict,
    per_tail: dict,
    n_members: int,
    failures: list,
    status: str,
    exclude_first: int,
    t_list=DEFAULT_T_LIST,
) -> dict:
    """The run's report card: one row per model, the only place a partial shows."""
    row = {
        "run_id": run_id,
        "meetreeks_id": meetreeks_id,
        "series_name": series_name,
        "model_type": "tarso",
        "status": status,
        "n_members": n_members,
        "n_failed_members": len(failures),
        "failure_detail": "; ".join(f"{m}: {e}" for m, e in failures) or None,
        "exclude_first": exclude_first,
        "n_dry": 0,
        "n_wet": 0,
        "first_year": None,
        "last_year": None,
    }

    any_frame = next((f for f in pooled.values() if f is not None and not f.empty), None)
    if any_frame is not None:
        row["first_year"] = int(any_frame["year"].min())
        row["last_year"] = int(any_frame["year"].max())
    for tail in TAILS:
        frame = pooled.get(tail)
        row[f"n_{tail}"] = 0 if frame is None else len(frame)

    for tail in TAILS:
        prefix = TAIL_PREFIX[tail]
        if tail not in per_tail:
            row.update({f"{prefix}_family": None, f"{prefix}_verdict": None,
                        f"{prefix}_shape": None, f"{prefix}_shape_se": None,
                        f"{prefix}_review_flag": None})
            for T in (10, 100, 1000):
                row[f"{prefix}_gev_T{T}"] = None
            continue
        selection = per_tail[tail]["selection"]
        levels = per_tail[tail]["levels"]
        row.update(
            {
                f"{prefix}_family": selection.family,
                f"{prefix}_verdict": selection.verdict,
                f"{prefix}_shape": selection.shape,
                f"{prefix}_shape_se": selection.shape_se,
                f"{prefix}_review_flag": selection.review_flag,
            }
        )
        for T in (10, 100, 1000):
            value = levels["gev"].get(T, float("nan")) if T in levels.index else float("nan")
            row[f"{prefix}_gev_T{T}"] = None if pd.isna(value) else float(value)

    row["review_flag"] = bool(
        any(per_tail[t]["selection"].review_flag for t in per_tail)
    ) if per_tail else True
    return row
