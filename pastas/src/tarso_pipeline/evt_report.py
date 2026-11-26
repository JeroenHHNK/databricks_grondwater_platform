"""Assembling the extremes stage's tables (handoff section 6).

``csv/<series>_return_periods.csv``   T x tail x family levels at the default threshold
``evt_selection.csv``                 per tail: is it fit to read (flag), threshold, shapes
``<run>_extremes_summary.csv``        the run's report card, one row per model
``pooled_extremes/<series>_{dry,wet}.csv``  every pooled extreme, re-fittable
``tail_parameters.csv``               parameters per family per tail
``used_extremes/used_extremes_<series>.csv``  which extremes the trimmed fit saw
``<prefix>_return_levels`` (Delta)    level per tail x family x T, and for ``gpd``/``exp``
                                      per threshold (``n_kept``) of the scan

No family is chosen here: a person picks ``gpd`` or ``exp`` per tail, and a threshold,
from the plots (``family_choice``). ``evt_selection`` keeps its name for continuity but
holds diagnostics only.

All levels are reported in **head units (metres NAP)**, not the maxima space the
fitting happens in: the dry tail is flipped back on the way out.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from tarso_pipeline.evt import (
    DEFAULT_KEPT_GRID,
    DEFAULT_T_LIST,
    FAMILIES,
    TRIMMED_FAMILIES,
    as_maxima,
    diagnose,
    empirical_level,
    empirical_points,
    fit_all_families,
    from_maxima,
    snap_exclude_first,
    tail_threshold,
    threshold_scan,
)

TAILS = ("dry", "wet")
TAIL_PREFIX = {"dry": "dr", "wet": "wet"}
VALUE_COLUMN = {"dry": "head_min", "wet": "head_max"}
LEVEL_FAMILIES = (*FAMILIES, "emp")


def tail_tables(
    values: np.ndarray,
    tail: str,
    exclude_first: int = 0,
    t_list=DEFAULT_T_LIST,
    kept_grid=DEFAULT_KEPT_GRID,
) -> dict:
    """Everything for one tail: levels, parameters, the threshold scan, diagnostics.

    ``levels`` are in head units at the default threshold: ``exclude_first`` snapped to
    the nearest ``n_kept`` of ``kept_grid`` (:func:`snap_exclude_first`), so the default
    is one of the scan's thresholds. The scan repeats ``gpd``/``exp`` for every ``n_kept``.
    """
    maxima = as_maxima(values, tail)
    n_total = len(maxima)
    exclude_first = snap_exclude_first(n_total, exclude_first, kept_grid)
    parameters, levels_maxima = fit_all_families(maxima, exclude_first, t_list)
    points = empirical_points(maxima, 0)

    levels = pd.DataFrame(index=levels_maxima.index)
    for family in FAMILIES:
        levels[family] = from_maxima(levels_maxima[family].to_numpy(), tail)
    levels["emp"] = from_maxima(np.array([empirical_level(points, T) for T in t_list]), tail)

    n_kept_default = n_total - exclude_first
    scan = threshold_scan(maxima, (*kept_grid, n_kept_default), t_list)
    return {
        "levels": levels,
        "parameters": parameters,
        "points": points,
        "maxima": maxima,
        "scan": scan,
        "diagnosis": diagnose(maxima, exclude_first),
        "exclude_first": exclude_first,
        "n_kept_default": n_kept_default,
        "threshold": float(from_maxima(tail_threshold(maxima, exclude_first), tail)),
    }


def return_period_table(per_tail: dict, t_list=DEFAULT_T_LIST) -> pd.DataFrame:
    """``T x {dr_,wet_} x {gpd,exp,gev,emp}`` at the default threshold."""
    out = pd.DataFrame(index=pd.Index(t_list, name="return_period"))
    for tail in TAILS:
        if tail not in per_tail:
            continue
        prefix = TAIL_PREFIX[tail]
        levels = per_tail[tail]["levels"]
        for family in LEVEL_FAMILIES:
            out[f"{prefix}_{family}"] = levels[family]
    return out


RETURN_LEVEL_COLUMNS = ["run_id", "meetreeks_id", "series_name", "model_type", "tail", "family",
                        "return_period", "level", "n_kept", "default_kept"]


def _level(value) -> float | None:
    return None if value is None or pd.isna(value) else float(value)


def return_level_rows(
    meetreeks_id: str, series_name: str, run_id: str, per_tail: dict,
    model_type: str = "tarso",
) -> list[dict]:
    """The ``<prefix>_return_levels`` rows: one per tail x family x T, and for ``gpd`` /
    ``exp`` per threshold of the scan (``n_kept``). ``emp`` and ``gev`` use every value,
    so their ``n_kept`` is the number of blocks. ``default_kept`` marks the default
    threshold. Levels in head units (m NAP); NaN kept as None.
    """
    rows = []
    for tail in TAILS:
        if tail not in per_tail:
            continue
        data = per_tail[tail]
        n_total = len(data["maxima"])
        base = {"run_id": run_id, "meetreeks_id": meetreeks_id, "series_name": series_name,
                "model_type": model_type, "tail": tail}
        for family in ("emp", "gev"):
            for period, level in data["levels"][family].items():
                rows.append({**base, "family": family, "return_period": int(period), "level": _level(level),
                             "n_kept": n_total, "default_kept": True})
        for scan_row in data["scan"].to_dict("records"):
            for family in TRIMMED_FAMILIES:
                for period in data["levels"].index:
                    value = scan_row.get(f"{family}_T{int(period)}")
                    rows.append({**base, "family": family, "return_period": int(period),
                                 "level": _level(None if value is None else float(from_maxima(value, tail))),
                                 "n_kept": int(scan_row["n_kept"]),
                                 "default_kept": int(scan_row["n_kept"]) == data["n_kept_default"]})
    return rows


DIAGNOSTIC_COLUMNS = ["run_id", "meetreeks_id", "series_name", "model_type", "tail", "n_blocks",
                      "spread_m", "flag", "reason", "review_flag", "exclude_first", "n_kept",
                      "threshold", "gpd_shape", "gpd_shape_se", "gev_shape"]


def selection_rows(meetreeks_id: str, series_name: str, run_id: str, per_tail: dict,
                   model_type: str = "tarso") -> list[dict]:
    """One row per tail for ``evt_selection.csv``: diagnostics, no family choice."""
    rows = []
    for tail in TAILS:
        if tail not in per_tail:
            continue
        data = per_tail[tail]
        gpd = data["parameters"].get("gpd")
        gev = data["parameters"].get("gev")
        default = data["scan"][data["scan"]["n_kept"] == data["n_kept_default"]]
        rows.append({
            "run_id": run_id, "meetreeks_id": meetreeks_id, "series_name": series_name,
            "model_type": model_type, "tail": tail,
            **data["diagnosis"],
            "review_flag": bool(data["diagnosis"]["flag"]),
            "exclude_first": data["exclude_first"], "n_kept": data["n_kept_default"],
            "threshold": data["threshold"],
            "gpd_shape": gpd["params"][0] if gpd else None,
            "gpd_shape_se": float(default["gpd_shape_se"].iloc[0]) if len(default) else None,
            "gev_shape": gev["params"][0] if gev else None,
        })
    return rows


def tail_parameter_rows(
    meetreeks_id: str, series_name: str, run_id: str, per_tail: dict,
    model_type: str = "tarso",
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
                "model_type": model_type,
                "tail": tail,
                "family": family,
            }
            if info is None:
                base.update(
                    {"shape": None, "loc": None, "scale": None, "n_total": None,
                     "n_fitted": None, "exceedance_rate": None, "threshold": None, "fit_ok": False}
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
                        "exceedance_rate": info["exceedance_rate"],
                        "threshold": info.get("threshold"), "fit_ok": True,
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
    model_type: str = "tarso",
) -> dict:
    """The run's report card: one row per model, the only place a partial shows."""
    row = {
        "run_id": run_id,
        "meetreeks_id": meetreeks_id,
        "series_name": series_name,
        "model_type": model_type,
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
        data = per_tail.get(tail)
        levels = data["levels"] if data else None
        gpd = data["parameters"].get("gpd") if data else None

        def at(family: str, T: int, levels=levels):
            if levels is None or T not in levels.index:
                return None
            return _level(levels.loc[T, family])

        row.update({
            f"{prefix}_flag": data["diagnosis"]["flag"] if data else None,
            f"{prefix}_gpd_shape": gpd["params"][0] if gpd else None,
            f"{prefix}_emp_T10": at("emp", 10),
            f"{prefix}_emp_T100": at("emp", 100),
            f"{prefix}_gpd_T1000": at("gpd", 1000),
            f"{prefix}_exp_T1000": at("exp", 1000),
        })

    row["review_flag"] = bool(
        any(per_tail[t]["diagnosis"]["flag"] for t in per_tail)
    ) if per_tail else True
    return row
