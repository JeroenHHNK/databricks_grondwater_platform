"""Select per scenario as the pipeline does, check the picks on IMPREX, count what passes.

    python research/accepted/report.py local_runs/research/accepted [--no-extremes] [--workers 7]

A scenario is a set of allowed model types: ``baseline`` (the pipeline's five types) and
``baseline + <group>`` for every variant group, plus ``all_ongoing`` (everything except
stresses that are not published any more, i.e. the HHNK files, and the RD shift test).
For each scenario ``tarso_pipeline.selection.select_models`` picks one model per series
from ``fit_metrics.csv`` + ``model_checks.csv`` + the IMPREX extremes.

Extremes: the picks without extremes are simulated on the 16 IMPREX members
(``extremes_run.run_model``; extra stresses held at their calibration mean by
``simulate_member``; members scaled per calendar month to the local forcing when the
model was calibrated on it), the selection is repeated, and so on until every pick has
extremes, so the runaway rule applies exactly as in the pipeline.
"""

from __future__ import annotations

import argparse
import sys
import warnings
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd

PASTAS = Path(__file__).resolve().parents[2]
for path in (PASTAS, PASTAS / "src"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from research.accepted import drought, stresses, variants  # noqa: E402
from tarso_pipeline.inputs import scale_members  # noqa: E402
from tarso_pipeline.selection import STAMP_CHECKS, select_models  # noqa: E402

IMPREX_DIR = PASTAS / "data" / "forcing"
NOT_ONGOING = ("wlhhnk", "localP0")
# A small menu fixed before looking at per-series results: two physical stresses that
# are measured, ongoing and site-relevant (Edam lake level, air pressure), on the
# pipeline's model types and the bucket. Guards against "with 69 variants one passes".
LEAN = ("wl", "wl1", "baro", "wl+baro", "wl1+baro")


def group_of(model_type: str) -> str:
    base, parts = variants.parse(model_type)
    if not parts:
        return "flex" if base in variants.FLEX else "baseline"
    return "+".join(parts) if base not in variants.FLEX else "flex@" + "+".join(parts)


def scenarios(types: list[str]) -> dict[str, list[str]]:
    base = [t for t in types if group_of(t) == "baseline"]
    groups = sorted({group_of(t) for t in types} - {"baseline"})
    out = {"baseline": base}
    for g in groups:
        out[f"+{g}"] = base + [t for t in types if group_of(t) == g]
    out["lean"] = base + [t for t in types if group_of(t) in LEAN or group_of(t) in {f"flex@{g}" for g in LEAN}]
    out["all_ongoing"] = [t for t in types if not any(p in NOT_ONGOING for p in variants.parse(t)[1])]
    return out


# --- extremes -----------------------------------------------------------------------

def monthly_factors(local: pd.Series, reference: pd.Series) -> pd.Series:
    """Mean per calendar month, local over reference, on the days both have."""
    both = pd.concat([local, reference], axis=1, keys=["local", "ref"]).dropna()
    means = both.groupby(both.index.month).mean()
    return (means["local"] / means["ref"]).reindex(range(1, 13)).fillna(1.0)


def _members():
    from tarso_pipeline.imprex import discover_members, load_member

    members = discover_members(str(IMPREX_DIR), "berkhout")
    return members, {m.member: load_member(m, units="m") for m in members}


def _radar_factors(meetreeks_id: str) -> pd.Series | None:
    """Radar over Berkhout per calendar month for the series' polder (as the pipeline's
    ``InputLibrary.imprex_factors``); None outside a radar polder."""
    from research import data
    from tarso_pipeline.inputs import radar_filename

    where = stresses.positions()
    polder = where["polder"].get(meetreeks_id) if meetreeks_id in where.index else None
    path = IMPREX_DIR / radar_filename(polder) if isinstance(polder, str) else None
    if path is None or not path.is_file():
        return None
    frame = pd.read_csv(path, parse_dates=["date"]).set_index("date")
    radar = frame.loc[frame["precipitation_source"].astype(str).str.startswith("radar"), "precipitation_mm"]
    precipitation, _ = data.load_forcing()
    return monthly_factors(radar / 1000.0, precipitation)


def _extremes_one(row: dict, out: str) -> dict:
    warnings.filterwarnings("ignore")
    from research import data
    from tarso_pipeline.extremes_run import run_model

    members, forcing = _members()
    _, parts = variants.parse(row["model_type"])
    if "localP" in parts or "localPE" in parts or "localP0" in parts:
        where = stresses.series_locations()[row["meetreeks_id"]]
        precipitation, evaporation = data.load_forcing()
        fp = monthly_factors(stresses.local_precipitation(where) / 1000.0, precipitation)
        fe = (monthly_factors(stresses.local_evaporation(where) / 1000.0, evaporation)
              if "localPE" in parts else pd.Series(1.0, index=range(1, 13)))
        forcing = {k: (p * fp.loc[p.index.month].to_numpy(), e * fe.loc[e.index.month].to_numpy())
                   for k, (p, e) in forcing.items()}
    if "radar" in parts:
        forcing = scale_members(forcing, _radar_factors(row["meetreeks_id"]))
    dry = [p for p in parts if drought.is_part(p)]
    if dry:  # the indicator rebuilt from each member's own (scaled) P and E, not held
        forcing = {k: (p, e, {"drought": drought.from_part(dry[0], p * 1000.0, e * 1000.0)})
                   for k, (p, e, *_) in forcing.items()}
    result = run_model(row["model_path"], row["meetreeks_id"], row["series_name"], members,
                       run_directory=str(Path(out) / "extremes"), run_id="research", units="m",
                       make_plots=False, forcing=forcing, model_type=row["model_type"])
    return result.summary


def run_extremes(rows: pd.DataFrame, out: Path, workers: int) -> pd.DataFrame:
    with ProcessPoolExecutor(workers) as pool:
        summaries = list(pool.map(_extremes_one, rows.to_dict("records"), [str(out)] * len(rows)))
    return pd.DataFrame(summaries)


# --- selection + counts ----------------------------------------------------------------

def select(metrics, checks, extremes, allowed) -> pd.DataFrame:
    m = metrics[metrics["model_type"].isin(allowed)]
    c = checks[checks["model_type"].isin(allowed)]
    return select_models(m, c, extremes if extremes is not None and not extremes.empty else None)


def settle(metrics, checks, extremes, allowed, out: Path, workers: int, use_extremes: bool):
    """Select; simulate the picks that lack extremes; repeat until none lack them."""
    for _ in range(6):
        picked = select(metrics, checks, extremes, allowed)
        if not use_extremes:
            return picked, extremes
        chosen = picked[picked["selected"]]
        have = set() if extremes.empty else set(zip(extremes["meetreeks_id"], extremes["model_type"]))
        todo = chosen[[(a, b) not in have for a, b in zip(chosen["meetreeks_id"], chosen["model_type"])]]
        if todo.empty:
            return picked, extremes
        rows = todo[["meetreeks_id", "series_name", "model_type"]].merge(
            metrics[["meetreeks_id", "model_type", "model_path"]], on=["meetreeks_id", "model_type"])
        print(f"    extremes for {len(rows)} picks", flush=True)
        extremes = pd.concat([extremes, run_extremes(rows, out, workers)], ignore_index=True)
        extremes.to_csv(out / "extremes_summary.csv", index=False)
    return select(metrics, checks, extremes, allowed), extremes


def mcnemar_p(gained: int, lost: int) -> float:
    from scipy.stats import binomtest

    n = gained + lost
    return float(binomtest(min(gained, lost), n, 0.5).pvalue) if n else 1.0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("folder")
    parser.add_argument("--no-extremes", action="store_true")
    parser.add_argument("--workers", type=int, default=7)
    parser.add_argument("--only", default=None, help="comma-separated scenario names")
    args = parser.parse_args()
    out = Path(args.folder)
    metrics = pd.read_csv(out / "fit_metrics.csv")
    checks = pd.read_csv(out / "model_checks.csv")
    extremes_path = out / "extremes_summary.csv"
    extremes = pd.read_csv(extremes_path) if extremes_path.exists() else pd.DataFrame()

    plan = scenarios(sorted(metrics["model_type"].unique()))
    if args.only:
        plan = {k: v for k, v in plan.items() if k in args.only.split(",") or k == "baseline"}
    rows, picks = [], {}
    for name, allowed in plan.items():
        print(f"  scenario {name} ({len(allowed)} types)", flush=True)
        picked, extremes = settle(metrics, checks, extremes, allowed, out, args.workers, not args.no_extremes)
        chosen = picked[picked["selected"]].set_index("meetreeks_id")
        picks[name] = chosen
        row = {"scenario": name, "n_types": len(allowed), "series": len(chosen),
               "passed": int(chosen["passed"].sum()),
               "green": int((chosen["pass_rsq"] & chosen["pass_memory"] & chosen["pass_bounds"]).sum()),
               "runaway_picks": int(chosen["runaway"].sum()),
               "r2_median": chosen["r2"].median(), "r2_holdout_median": chosen["r2_holdout"].median()}
        for k in STAMP_CHECKS:
            row[f"fail_{k}"] = int((~chosen[f"pass_{k}"]).sum())
        rows.append(row)
        picked.to_csv(out / f"selection_{name.strip('+').replace('+', '_').replace('@', '-')}.csv", index=False)

    base = picks["baseline"]["passed"]
    table = pd.DataFrame(rows)
    gained, lost, p = [], [], []
    for name in table["scenario"]:
        now = picks[name]["passed"].reindex(base.index).fillna(False).astype(bool)
        g, l_ = int((now & ~base).sum()), int((~now & base).sum())
        gained.append(g)
        lost.append(l_)
        p.append(mcnemar_p(g, l_))
    table["gained"], table["lost"], table["p_mcnemar"] = gained, lost, p
    table.to_csv(out / "scenarios.csv", index=False)
    best = picks.get("all_ongoing")
    if best is not None:
        best.reset_index()[["meetreeks_id", "series_name", "model_type", "passed", "tier", "r2", "r2_holdout",
                            "runaway", "reason"]].to_csv(out / "picks_all_ongoing.csv", index=False)
        print(best["model_type"].map(group_of).value_counts().to_string())
    with pd.option_context("display.width", 200):
        print(table.round(3).to_string(index=False))


if __name__ == "__main__":
    main()
