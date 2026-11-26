"""Score every candidate and the Pastas baseline on the same series, folds and R2.

    python research/run_benchmark.py                                  # everything
    python research/run_benchmark.py --series 5 --methods pastas,arx,trees
    python research/run_benchmark.py --methods lstm,cnn --seeds 10 --out <folder>

Groups for ``--methods``: ``pastas`` (the 5 pipeline types), ``arx`` (dlm_ols, dlm_sarimax,
arx), ``trees`` (rf, lgbm, lgbm_global), ``lstm``, ``cnn``; single method names work too.
Writes ``scores.csv`` (series x method x fold) and ``simulations.parquet`` (every
fold, for the ensembles in ``summarise.py``) to ``local_runs/research/<date>/``; ``summarise.py`` turns them into the verdict.
A rerun into the same folder appends: methods already scored there are skipped.
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
import time
import traceback
import warnings
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import pandas as pd

PASTAS = Path(__file__).resolve().parents[1]
for path in (PASTAS, PASTAS / "src"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from research import data  # noqa: E402
from research.evaluate import FOLDS, make_task, score  # noqa: E402
from research.models import Context, arx, deep, pastas_models, trees  # noqa: E402

PER_SERIES = {
    **{f"pastas_{m}": (lambda m: lambda task, ctx: pastas_models.simulate(task, ctx, m))(m)
       for m in pastas_models.MODEL_TYPES},
    "dlm_ols": arx.simulate_dlm_ols,
    "dlm_sarimax": arx.simulate_dlm_sarimax,
    "arx": arx.simulate_arx,
    "rf": trees.simulate_rf,
    "lgbm": trees.simulate_lgbm,
}
GLOBAL = {"lgbm_global": trees.simulate_lgbm_global, "lstm": "lstm", "cnn": "cnn"}
GROUPS = {
    "pastas": [f"pastas_{m}" for m in pastas_models.MODEL_TYPES],
    "arx": ["dlm_ols", "dlm_sarimax", "arx"],
    "trees": ["rf", "lgbm", "lgbm_global"],
    "lstm": ["lstm"],
    "cnn": ["cnn"],
}

_CTX: Context | None = None


def _init(ctx: Context) -> None:
    global _CTX
    _CTX = ctx
    warnings.filterwarnings("ignore")


def _one_series(series, methods: list[str]) -> tuple[list[dict], list[pd.DataFrame]]:
    """Every per-series method on every fold of one series (runs in a worker)."""
    rows, sims = [], []
    for method in methods:
        for fold in FOLDS:
            task = make_task(series, fold)
            start = time.perf_counter()
            try:
                simulated = PER_SERIES[method](task, _CTX)
                rows.append(score(task, simulated, method, time.perf_counter() - start))
            except Exception as error:  # noqa: BLE001 - one failed fit is a row, not a crash
                rows.append(score(task, None, method, time.perf_counter() - start,
                                  error=f"{type(error).__name__}: {error}"[:300]))
                continue
            sims.append(_sim_frame(task, simulated, method))
    return rows, sims


def _sim_frame(task, simulated: pd.Series, method: str) -> pd.DataFrame:
    frame = pd.DataFrame({"simulated": simulated}).join(task.series.head.rename("observed"), how="left")
    frame = frame.rename_axis("datum").reset_index()
    frame["meetreeks_id"], frame["method"], frame["fold"], frame["split"] = (
        task.meetreeks_id, method, task.fold, task.split)
    return frame


def run_global(method: str, series: list, ctx: Context, seeds: int) -> tuple[list[dict], list[pd.DataFrame]]:
    rows, sims = [], []
    for fold in FOLDS:
        tasks = [make_task(s, fold) for s in series]
        start = time.perf_counter()
        try:
            if method == "lgbm_global":
                simulated = trees.simulate_lgbm_global(tasks, ctx)
            else:
                simulated = deep.simulate_all(tasks, ctx, GLOBAL[method], seeds=seeds)
            error = None
        except Exception as exc:  # noqa: BLE001
            traceback.print_exc()
            simulated, error = {}, f"{type(exc).__name__}: {exc}"[:300]
        seconds = (time.perf_counter() - start) / max(len(tasks), 1)  # per series, comparable
        for task in tasks:
            sim = simulated.get(task.meetreeks_id)
            rows.append(score(task, sim, method, seconds, error=error if sim is None else None))
            if sim is not None:
                sims.append(_sim_frame(task, sim, method))
        print(f"  {method:12} {fold:8} {time.perf_counter() - start:7.0f} s", flush=True)
    return rows, sims


def expand(methods: str) -> list[str]:
    out = []
    for name in methods.split(","):
        name = name.strip()
        for method in GROUPS.get(name, [name]):
            if method not in PER_SERIES and method not in GLOBAL:
                raise SystemExit(f"unknown method {method!r}")
            if method not in out:
                out.append(method)
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--methods", default="pastas,arx,trees,lstm,cnn")
    parser.add_argument("--series", type=int, default=None, help="only the first N series")
    parser.add_argument("--seeds", type=int, default=10, help="networks per deep ensemble")
    parser.add_argument("--workers", type=int, default=7)
    parser.add_argument("--out", default=None, help="run folder (default local_runs/research/<date>)")
    args = parser.parse_args()

    out = Path(args.out) if args.out else PASTAS / "local_runs" / "research" / dt.date.today().strftime("%y_%m_%d")
    out.mkdir(parents=True, exist_ok=True)
    scores_path, sims_path = out / "scores.csv", out / "simulations.parquet"
    old_scores = pd.read_csv(scores_path) if scores_path.exists() else pd.DataFrame()
    old_sims = pd.read_parquet(sims_path) if sims_path.exists() else pd.DataFrame()
    done = set(old_scores["method"]) if not old_scores.empty else set()

    methods = [m for m in expand(args.methods) if m not in done]
    if done & set(expand(args.methods)):
        print(f"already scored in {out}, skipped: {sorted(done & set(expand(args.methods)))}")
    precipitation, evaporation = data.load_forcing()
    series = data.load_series(precipitation, limit=args.series)
    ctx = Context.build(precipitation, evaporation)
    print(f"{len(series)} series, methods {methods}, out {out}", flush=True)

    rows, sims = [], []
    per_series = [m for m in methods if m in PER_SERIES]
    if per_series:
        start = time.perf_counter()
        with ProcessPoolExecutor(args.workers, initializer=_init, initargs=(ctx,)) as pool:
            for i, (r, s) in enumerate(pool.map(_one_series, series, [per_series] * len(series)), 1):
                rows += r
                sims += s
                print(f"  [{i}/{len(series)}] {series[i - 1].name}  {time.perf_counter() - start:.0f} s", flush=True)
    for method in [m for m in methods if m in GLOBAL]:
        r, s = run_global(method, series, ctx, args.seeds)
        rows += r
        sims += s

    scores = pd.concat([old_scores, pd.DataFrame(rows)], ignore_index=True)
    scores.to_csv(scores_path, index=False)
    if sims:
        pd.concat([old_sims, *sims], ignore_index=True).to_parquet(sims_path, index=False)
    print(f"wrote {scores_path} ({len(scores)} rows)")


if __name__ == "__main__":
    main()
