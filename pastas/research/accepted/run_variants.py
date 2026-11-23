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
from research.accepted import stresses, variants  # noqa: E402
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
    return {"precipitation": precipitation, "evaporation": evaporation, "extra": extra,
            "locations": stresses.series_locations()}


def inputs_for(series, shared: dict) -> variants.Inputs:
    head = series.head
    inputs = variants.Inputs(shared["precipitation"], shared["evaporation"])
    where = shared["locations"].get(series.meetreeks_id)
    if where is not None:
        inputs.local_precipitation = stresses.local_precipitation(where) / 1000.0
        inputs.local_precipitation_noshift = stresses.local_precipitation(where, shift_days=0) / 1000.0
        inputs.local_evaporation = stresses.local_evaporation(where) / 1000.0
    # Each extra stress minus its mean over this series' calibration days: the model learns
    # only its impulses, and on IMPREX it is held at that mean (anomaly 0).
    inputs.extra = {k: stresses.anomaly(v, head) for k, v in shared["extra"].items()
                    if v.loc[head.index.min():head.index.max()].count()}
    return inputs


def _init() -> None:
    warnings.filterwarnings("ignore")
    _SHARED.update(shared_inputs())


def fit_one(series, name: str, inputs, out: Path) -> tuple[dict, list]:
    ids = {"run_id": out.name, "meetreeks_id": series.meetreeks_id, "series_name": series.name, "model_type": name}
    row = empty_row()
    row.update(ids)
    row.update(observed_fields(series.head))
    checks: list = []
    start = time.perf_counter()
    try:
        model = variants.build(name, series.name, series.head, inputs)
        model.solve(tmin=series.tmin, tmax=series.head.index[-1].strftime("%Y-%m-%d"), report=False)
        row.update(extract_metrics(model, model_type=name))
        row.update(ids)
        checks = run_checks(model, ids)
        path = out / "models" / f"{series.name}_{name}.pas"
        path.parent.mkdir(parents=True, exist_ok=True)
        model.to_file(str(path))
        row["model_path"] = str(path)
        task = make_task(series, "holdout")
        try:
            refit = variants.build(name, series.name, task.train, inputs)
            refit.solve(tmin=series.tmin, tmax=task.tmax, report=False)
            row["r2_holdout"] = r2(task.evaluate, refit.simulate(tmin=series.head.index[0], tmax=series.head.index[-1]))
            row["holdout_share"] = 0.3
        except Exception:  # noqa: BLE001 - a failed second fit must not lose the first
            row["r2_holdout"] = None
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
    args = parser.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    metrics_path, checks_path = out / "fit_metrics.csv", out / "model_checks.csv"
    old_metrics = pd.read_csv(metrics_path) if metrics_path.exists() else pd.DataFrame()
    old_checks = pd.read_csv(checks_path) if checks_path.exists() else pd.DataFrame()
    done = set(old_metrics["model_type"]) if not old_metrics.empty else set()
    names = [n for n in variants.names(args.groups.split(",")) if n not in done]
    precipitation, _ = data.load_forcing()
    series = data.load_series(precipitation, limit=args.series)
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
