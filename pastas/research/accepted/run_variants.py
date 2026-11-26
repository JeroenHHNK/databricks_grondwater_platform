"""Fit every variant on every validated series, check it, and select as the pipeline does.

    python research/accepted/run_variants.py --groups baseline,localP,localP0,localPE,wl,wlmid,wlhhnk,baro,flex
    python research/accepted/run_variants.py --groups localP+wl,flex@localP --out <same folder>

Per series and variant: a full fit (``solve`` from the calibration start, as ``fit_series``),
``extract_metrics`` + ``observed_fields``, the Brakenhoff checks (``run_checks``), the
holdout R2 (fit on the first 70 %, simulate the rest, as ``holdout_r2``) and the ``.pas``.
Writes ``fit_metrics.csv`` and ``model_checks.csv`` in the pipeline's columns; a rerun into
the same folder skips variants already there.
"""

from __future__ import annotations

import argparse
import sys
import time
import warnings
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import pandas as pd

PASTAS = Path(__file__).resolve().parents[2]
for path in (PASTAS, PASTAS / "src"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from research import data  # noqa: E402
from research.accepted import drought, stresses, variants  # noqa: E402
from research.evaluate import make_task, r2  # noqa: E402
from tarso_pipeline.fit_metrics import empty_row, extract_metrics, observed_fields  # noqa: E402
from tarso_pipeline.model_checks import run_checks  # noqa: E402

_SHARED: dict = {}


def shared_inputs() -> dict:
    """Stresses that are the same for every series (loaded once per worker)."""
    precipitation, evaporation = data.load_forcing()
    extra = {"pressure": stresses.pressure(), "rws_edam": stresses.rws_level("edam"),
             "rws_markermeer.midden": stresses.rws_level("markermeer.midden"),
             "hhnk_drieban": stresses.hhnk_level("drieban")}
    try:
        boezem = stresses.boezem_level("beemster")
        extra.update({"boezem": boezem, "boezem_dev": stresses.setpoint_deviation(boezem)})
    except FileNotFoundError:
        pass  # the Dijkgraaf de Boer files were removed on 2026-11-24: the bz variants fail cleanly
    gemalen = {(name, column): stresses.gemaal_level(name, column)
               for name in stresses.GEMALEN for column in stresses.COLUMNS}
    radar = {}
    for polder in ("Beemster", "Purmer", "Heerhugowaard"):
        path = stresses.STRESSES.parent / "forcing" / "local" / f"radar_{polder.lower()}.csv"
        if path.exists():
            radar[polder] = pd.read_csv(path, parse_dates=["date"], index_col="date")["precipitation_mm"]
    return {"precipitation": precipitation, "evaporation": evaporation, "extra": extra,
            "locations": stresses.series_locations(), "positions": stresses.positions(),
            "gemalen": gemalen, "radar": radar}


def gemaal_stresses(meetreeks_id: str, shared: dict) -> dict:
    """``gemaal_up``/``_ip``/``_pos`` from the nearest station, ``..._far`` from the other one."""
    where = shared["positions"].loc[meetreeks_id] if meetreeks_id in shared["positions"].index else None
    if where is None or pd.isna(where["polder"]):
        return {}
    stations = stresses.stations_by_distance(where["lat"], where["lon"], where["polder"])
    out = {}
    for suffix, station in (("", stations[0] if stations else None),
                            ("_far", stations[1] if len(stations) > 1 else None)):
        if station is None:
            continue
        up = shared["gemalen"][(station[0], "uitstroompeil")]
        ip = shared["gemalen"][(station[0], "instroompeil")]
        out[f"gemaal_up{suffix}"], out[f"gemaal_ip{suffix}"] = up, ip
        if where["level"] == "upper":
            out[f"gemaal_pos{suffix}"] = up
        elif where["level"] == "lower":
            out[f"gemaal_pos{suffix}"] = ip
    return out


def radar_forcing(meetreeks_id: str, shared: dict, berkhout: pd.Series) -> pd.Series | None:
    """The polder's radar pixel (m/day, the 08-08 UT sums moved one day back like ``localP``),
    with Berkhout before the radar record starts (warm-up)."""
    where = shared["positions"].loc[meetreeks_id] if meetreeks_id in shared["positions"].index else None
    if where is None or where["polder"] not in shared["radar"]:
        return None
    radar = shared["radar"][where["polder"]].dropna() / 1000.0
    radar.index = radar.index - pd.Timedelta(days=1)
    before = berkhout.loc[:radar.index.min() - pd.Timedelta(days=1)]
    after = berkhout.loc[radar.index.max() + pd.Timedelta(days=1):]
    return pd.concat([before, radar, after]).asfreq("D").fillna(berkhout).rename("precipitation")


def inputs_for(series, shared: dict) -> variants.Inputs:
    head = series.head
    inputs = variants.Inputs(shared["precipitation"], shared["evaporation"])
    where = shared["locations"].get(series.meetreeks_id)
    if where is not None:
        inputs.local_precipitation = stresses.local_precipitation(where) / 1000.0
        inputs.local_precipitation_noshift = stresses.local_precipitation(where, shift_days=0) / 1000.0
        inputs.local_evaporation = stresses.local_evaporation(where) / 1000.0
    inputs.radar_precipitation = radar_forcing(series.meetreeks_id, shared, shared["precipitation"])
    # Each extra stress minus its mean over this series' calibration days: the model learns
    # only its impulses, and on IMPREX it is held at that mean (anomaly 0).
    extra = {**shared["extra"], **gemaal_stresses(series.meetreeks_id, shared)}
    inputs.extra = {k: stresses.anomaly(v, head) for k, v in extra.items()
                    if v.loc[head.index.min():head.index.max()].count()}
    return inputs


def _init() -> None:
    warnings.filterwarnings("ignore")
    _SHARED.update(shared_inputs())


DROUGHT = ("2026-07-01", "2026-08-15")   # 5 mm of rain in July 2026 against 114 mm Makkink
RECOVERY = ("2026-08-16", "2026-09-30")
SPRING = ("2025-03-01", "2025-05-31")    # the dry spring of 2025
JUNE, END_JULY = ("2026-06-01", "2026-06-20"), ("2026-07-25", "2026-08-02")
LEFT_OUT = ("2026-06-01", "2026-09-30")  # masked in the leave-the-drought-out refit
MIN_DAYS = 10


def _rmse(observed: pd.Series, simulated: pd.Series, window: tuple) -> float | None:
    o, s = observed.loc[window[0]:window[1]].align(simulated.loc[window[0]:window[1]], join="inner")
    keep = o.notna() & s.notna()
    return float((((o - s)[keep]) ** 2).mean() ** 0.5) if keep.sum() >= MIN_DAYS else None


def drought_scores(observed: pd.Series, simulated: pd.Series) -> dict:
    """RMSE in the 2026 drought, its recovery and the 2025 spring; the error on the Jun -> Jul drop."""
    out = {"rmse_drought": _rmse(observed, simulated, DROUGHT),
           "rmse_recovery": _rmse(observed, simulated, RECOVERY),
           "rmse_spring": _rmse(observed, simulated, SPRING)}
    o_june, o_july = observed.loc[JUNE[0]:JUNE[1]], observed.loc[END_JULY[0]:END_JULY[1]]
    if o_june.count() >= 5 and o_july.count() >= 3:
        obs_drop = float(o_july.mean() - o_june.mean())
        sim_drop = float(simulated.loc[END_JULY[0]:END_JULY[1]].mean() - simulated.loc[JUNE[0]:JUNE[1]].mean())
        out.update({"drop_observed": obs_drop, "drop_simulated": sim_drop})
    return out


_NESTED: dict = {}  # per worker: the optimal parameters of the nested fits


def _sse(model) -> float:
    residuals = model.residuals()
    return float((residuals ** 2).sum()) if residuals is not None else float("inf")


def solve_variant(name: str, series_name: str, head: pd.Series, inputs, tmin: str, tmax: str):
    """Build and solve; a drought variant is solved *nested* on the same model without it.

    The drought gain's default start (+1/std) has the sign opposite to the usual effect, and a
    fit from there can end worse than the model without the term. So the model without it
    is fitted first, its parameters start the drought model, and the drought gain starts at
    a small value of either sign; the lower sum of squares wins. Same data, so no leak.
    """
    base, parts = variants.parse(name)
    dry = [p for p in parts if drought.is_part(p)]
    model = variants.build(name, series_name, head, inputs)
    if not dry:
        model.solve(tmin=tmin, tmax=tmax, report=False)
        return model
    rest = "+".join(p for p in parts if p not in dry)
    nested_name = f"{base}@{rest}" if rest else base
    key = (series_name, nested_name, tmin, tmax, len(head), head.index[-1])
    if key not in _NESTED:  # the same nested fit serves every drought setting of this series
        nested = variants.build(nested_name, series_name, head, inputs)
        nested.solve(tmin=tmin, tmax=tmax, report=False)
        _NESTED[key] = nested.parameters["optimal"].copy()
    start = _NESTED[key]
    scale = float(model.stressmodels["drought"].stress[0].series.std()) or 1.0
    best = None
    for sign in (-1.0, 1.0):
        trial = variants.build(name, series_name, head, inputs)
        for parameter, value in start.items():
            if parameter in trial.parameters.index and pd.notna(value):
                trial.set_parameter(parameter, initial=float(value))
        trial.set_parameter("drought_A", initial=sign * 0.1 / scale)
        try:
            trial.solve(tmin=tmin, tmax=tmax, report=False)
        except Exception:  # noqa: BLE001 - the other sign may still solve
            continue
        if best is None or _sse(trial) < _sse(best):
            best = trial
    if best is None:
        raise RuntimeError("drought model did not solve from either sign")
    return best


def fit_one(series, name: str, inputs, out: Path) -> tuple[dict, list]:
    ids = {"run_id": out.name, "meetreeks_id": series.meetreeks_id, "series_name": series.name, "model_type": name}
    row = empty_row()
    row.update(ids)
    row.update(observed_fields(series.head))
    checks: list = []
    start = time.perf_counter()
    try:
        model = solve_variant(name, series.name, series.head, inputs, series.tmin,
                              series.head.index[-1].strftime("%Y-%m-%d"))
        row.update(extract_metrics(model, model_type=name))
        row.update(ids)
        checks = run_checks(model, ids)
        path = out / "models" / f"{series.name}_{name}.pas"
        path.parent.mkdir(parents=True, exist_ok=True)
        model.to_file(str(path))
        row["model_path"] = str(path)
        task = make_task(series, "holdout")
        try:
            refit = solve_variant(name, series.name, task.train, inputs, series.tmin, task.tmax)
            row["r2_holdout"] = r2(task.evaluate, refit.simulate(tmin=series.head.index[0], tmax=series.head.index[-1]))
            row["holdout_share"] = 0.3
        except Exception:  # noqa: BLE001 - a failed second fit must not lose the first
            row["r2_holdout"] = None
        row.update(drought_scores(series.head, model.simulate(tmin=series.head.index[0],
                                                              tmax=series.head.index[-1])))
        masked = series.head.drop(series.head.loc[LEFT_OUT[0]:LEFT_OUT[1]].index)
        if series.head.loc[DROUGHT[0]:DROUGHT[1]].count() >= MIN_DAYS and masked.count() > 100:
            try:
                refit = solve_variant(name, series.name, masked, inputs, series.tmin,
                                      series.head.index[-1].strftime("%Y-%m-%d"))
                row["rmse_drought_left_out"] = _rmse(series.head, refit.simulate(
                    tmin=series.head.index[0], tmax=series.head.index[-1]), DROUGHT)
            except Exception:  # noqa: BLE001
                row["rmse_drought_left_out"] = None
        row["success"] = True
    except Exception as error:  # noqa: BLE001
        row.update({"success": False, "error_message": f"{type(error).__name__}: {error}"[:300]})
    row["fit_seconds"] = round(time.perf_counter() - start, 2)
    return row, checks


def one_series(series, names: list[str], out: Path) -> tuple[list, list]:
    inputs = inputs_for(series, _SHARED)
    rows, checks = [], []
    for name in names:
        if not variants.applicable(name):
            continue
        row, check_rows = fit_one(series, name, inputs, out)
        rows.append(row)
        checks += check_rows
    return rows, checks


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--groups", default="baseline")
    parser.add_argument("--series", type=int, default=None)
    parser.add_argument("--workers", type=int, default=7)
    parser.add_argument("--out", default=str(PASTAS / "local_runs" / "research" / "accepted"))
    parser.add_argument("--ids-from", default=None,
                        help="a CSV with meetreeks_id: fit only those series (freezes the validated set)")
    parser.add_argument("--raw-polder", default=None,
                        help="fit that polder's *unvalidated* series (raw export, pipeline cleaning) instead")
    parser.add_argument("--types", default=None,
                        help="comma-separated base types to keep (e.g. linear_gamma,linear_dexp)")
    args = parser.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    metrics_path, checks_path = out / "fit_metrics.csv", out / "model_checks.csv"
    old_metrics = pd.read_csv(metrics_path) if metrics_path.exists() else pd.DataFrame()
    old_checks = pd.read_csv(checks_path) if checks_path.exists() else pd.DataFrame()
    done = set(old_metrics["model_type"]) if not old_metrics.empty else set()
    names = [n for n in variants.names(args.groups.split(",")) if n not in done]
    if args.types:
        keep_types = set(args.types.split(","))
        names = [n for n in names if variants.parse(n)[0] in keep_types]
    precipitation, _ = data.load_forcing()
    if args.raw_polder:
        where = stresses.positions()
        ids = set(where.index[where["polder"] == args.raw_polder])
        series = data.load_series(precipitation, limit=args.series, heads=data.raw_heads(ids))
    else:
        series = data.load_series(precipitation, limit=args.series)
    if args.ids_from:
        keep = set(pd.read_csv(args.ids_from, usecols=["meetreeks_id"])["meetreeks_id"])
        series = [s for s in series if s.meetreeks_id in keep]
    print(f"{len(series)} series x {len(names)} variants -> {out}", flush=True)
    rows, checks = [], []
    start = time.perf_counter()
    with ProcessPoolExecutor(args.workers, initializer=_init) as pool:
        for i, (r, c) in enumerate(pool.map(one_series, series, [names] * len(series), [out] * len(series)), 1):
            rows += r
            checks += c
            print(f"  [{i}/{len(series)}] {time.perf_counter() - start:.0f} s", flush=True)
    pd.concat([old_metrics, pd.DataFrame(rows)], ignore_index=True).to_csv(metrics_path, index=False)
    pd.concat([old_checks, pd.DataFrame(checks)], ignore_index=True).to_csv(checks_path, index=False)
    print(f"wrote {metrics_path}")


if __name__ == "__main__":
    main()
