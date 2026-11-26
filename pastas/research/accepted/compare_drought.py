"""Drought indicators: accepted models and the drought fit, one indicator setting at a time.

    python research/accepted/compare_drought.py local_runs/research/drought

The reference is the pipeline's extended set (``baseline`` + ``wl1+baro`` + ``radar+wl1+baro``,
linear and bucket types). Each scenario adds *one* indicator setting ``X`` for every series
(``wl1+baro+X`` and ``radar+wl1+baro+X``): one global choice, not one per series. Selection
with the pipeline's ``select_models`` (no extremes here; those follow for the winner).

Per scenario:
- passed, gained and lost against the reference (McNemar p);
- the median holdout R2 of the picks;
- the drought fit of the picks against the reference picks:
  - the RMSE 1 Jul-15 Aug 2026 in the full fit and with Jun-Sep 2026 left out of the fit;
  - the error on the Jun -> end-of-Jul drop;
  - the recovery RMSE;
  - the share of series that improved (sign test), over all series and the drought-misfit ones.

A drought misfit is a reference pick whose drought RMSE is above 1.5 x its overall RMSE.
Its direction is from the drop: "too high" (it drops less than observed) or "too low".

Writes ``drought_scenarios.csv``, ``drought_series.csv`` (per series: reference against the
best scenario) and ``picks_drought_<X>.csv``.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

PASTAS = Path(__file__).resolve().parents[2]
for path in (PASTAS, PASTAS / "src"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from research.accepted import drought, stresses, variants  # noqa: E402
from research.accepted.report import mcnemar_p, select, settle  # noqa: E402

REFERENCE_GROUPS = ("", "wl1+baro", "radar+wl1+baro")
SCORES = ("rmse_drought", "rmse_drought_left_out", "rmse_recovery", "rmse_spring", "drop_error")
MISFIT = 1.5


def indicator_of(model_type: str) -> str | None:
    _, parts = variants.parse(model_type)
    found = [p for p in parts if drought.is_part(p)]
    return found[0] if found else None


def reference_types(metrics: pd.DataFrame) -> list[str]:
    keep = []
    for name in metrics["model_type"].unique():
        base, parts = variants.parse(name)
        if "+".join(parts) in REFERENCE_GROUPS and not (base in variants.FLEX and not parts):
            keep.append(name)
    return sorted(keep)


def sign_p(better: int, worse: int) -> float:
    return mcnemar_p(better, worse)  # the same binomial test


def picks_with_scores(metrics, checks, allowed) -> pd.DataFrame:
    chosen = select(metrics, checks, None, allowed)
    chosen = chosen[chosen["selected"]][["meetreeks_id", "model_type", "passed", "r2_holdout"]]
    cols = ["meetreeks_id", "model_type", "rmse", *[c for c in SCORES if c in metrics], "model_path"]
    return chosen.merge(metrics[cols], on=["meetreeks_id", "model_type"], how="left").set_index("meetreeks_id")


def paired(metrics: pd.DataFrame) -> pd.DataFrame:
    """Like for like: each drought model against the same series, base type and forcing without it.

    The selection mixes the indicator with which type wins; this isolates the indicator.
    The leave-the-drought-out RMSE is the out-of-sample test, the holdout R2 the second one.
    """
    ok = metrics[metrics["success"].astype(bool)].copy()
    ok["base"] = ok["model_type"].map(lambda n: variants.parse(n)[0])
    ok["rest"] = ok["model_type"].map(
        lambda n: "+".join(p for p in variants.parse(n)[1] if not drought.is_part(p)))
    nested = ok[ok["indicator"].isna()].set_index(["meetreeks_id", "base", "rest"])
    rows = []
    for indicator, group in ok[ok["indicator"].notna()].groupby("indicator"):
        joined = group.set_index(["meetreeks_id", "base", "rest"]).join(nested, rsuffix="_nested", how="inner")
        d_aic = joined["aic"] - joined["aic_nested"]
        d_out = (joined["rmse_drought_left_out"] - joined["rmse_drought_left_out_nested"]).dropna()
        d_in = (joined["rmse_drought"] - joined["rmse_drought_nested"]).dropna()
        d_ho = (joined["r2_holdout"] - joined["r2_holdout_nested"]).dropna()
        better, worse = int((d_out < -1e-4).sum()), int((d_out > 1e-4).sum())
        rows.append({"indicator": indicator, "models": len(joined), "types": joined.index.get_level_values("base").nunique(),
                     "d_aic_median": round(d_aic.median(), 1), "share_d_aic_below_-10": round((d_aic < -10).mean(), 2),
                     "d_rmse_drought_cm": round(100 * d_in.median(), 2),
                     "d_rmse_left_out_cm": round(100 * d_out.median(), 2),
                     "left_out_better": better, "left_out_worse": worse, "p_left_out": round(sign_p(better, worse), 4),
                     "d_r2_holdout_median": round(d_ho.median(), 3),
                     "holdout_better": int((d_ho > 0.005).sum()), "holdout_worse": int((d_ho < -0.005).sum())})
    return pd.DataFrame(rows).sort_values("d_rmse_left_out_cm")


def with_extremes(metrics, checks, out: Path, scenarios: dict, workers: int) -> pd.DataFrame:
    """Settle each scenario with the IMPREX extremes (runaway rule) and check dry T100 against the record low."""
    path = out / "extremes_summary.csv"
    extremes = pd.read_csv(path) if path.exists() else pd.DataFrame()
    rows, base = [], None
    for name, allowed in scenarios.items():
        print(f"  extremes: scenario {name}", flush=True)
        picked, extremes = settle(metrics, checks, extremes, allowed, out, workers, True)
        chosen = picked[picked["selected"]].set_index("meetreeks_id")
        chosen.reset_index().to_csv(out / f"selection_drought_{name}.csv", index=False)
        passed = chosen["passed"].astype(bool)
        base = passed if base is None else base
        now = passed.reindex(base.index).fillna(False)
        gained, lost = int((now & ~base).sum()), int((~now & base).sum())
        ok = chosen[passed]
        below = (ok["dry_T100"] < ok["min_value"]).mean() if len(ok) else np.nan
        rows.append({"scenario": name, "passed": int(passed.sum()), "gained": gained, "lost": lost,
                     "p": round(mcnemar_p(gained, lost), 4), "runaway_picks": int(chosen["runaway"].sum()),
                     "runaway_models_avoided": int(picked.loc[picked["runaway"].fillna(False).astype(bool), "meetreeks_id"].nunique()),
                     "picks_with_indicator": int(chosen["model_type"].map(indicator_of).notna().sum()),
                     "r2_holdout_median": round(chosen["r2_holdout"].median(), 3),
                     "share_dry_T100_below_record_low": round(float(below), 2),
                     "dry_T100_minus_record_low_median_cm": round(100 * float((ok["dry_T100"] - ok["min_value"]).median()), 1)})
    table = pd.DataFrame(rows)
    table.to_csv(out / "drought_extremes.csv", index=False)
    print(table.to_string(index=False))
    return table


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("folder", nargs="?", default=str(PASTAS / "local_runs" / "research" / "drought"))
    parser.add_argument("--extremes", default=None,
                        help="comma-separated indicator settings to settle with IMPREX extremes (e.g. bkt100l7)")
    parser.add_argument("--workers", type=int, default=7)
    args = parser.parse_args()
    out = Path(args.folder)
    metrics = pd.read_csv(out / "fit_metrics.csv", low_memory=False)
    checks = pd.read_csv(out / "model_checks.csv", low_memory=False)
    metrics["drop_error"] = metrics["drop_simulated"] - metrics["drop_observed"]
    metrics["indicator"] = metrics["model_type"].map(indicator_of)
    where = stresses.positions()

    like_for_like = paired(metrics)
    like_for_like.to_csv(out / "drought_paired.csv", index=False)
    print(like_for_like.to_string(index=False), end="\n\n")
    base_types = reference_types(metrics)
    reference = picks_with_scores(metrics, checks, base_types)
    reference.reset_index().to_csv(out / "picks_drought_reference.csv", index=False)
    misfit = reference["rmse_drought"] > MISFIT * reference["rmse"]
    direction = np.where(reference["drop_error"] > 0, "too high", "too low")
    print(f"reference: {int(reference['passed'].sum())} of {len(reference)} pass; "
          f"drought misfits {int(misfit.sum())} ({(direction[misfit] == 'too high').sum()} too high)")

    rows, per_series = [], {}
    for indicator in sorted(metrics["indicator"].dropna().unique()):
        allowed = base_types + sorted(metrics.loc[metrics["indicator"] == indicator, "model_type"].unique())
        picks = picks_with_scores(metrics, checks, allowed).reindex(reference.index)
        picks.reset_index().to_csv(out / f"picks_drought_{indicator}.csv", index=False)
        per_series[indicator] = picks
        passed = picks["passed"].fillna(False).astype(bool)
        base = reference["passed"].astype(bool)
        gained, lost = int((passed & ~base).sum()), int((~passed & base).sum())
        row = {"indicator": indicator, "passed": int(passed.sum()), "gained": gained, "lost": lost,
               "p_passed": round(mcnemar_p(gained, lost), 4),
               "picks_with_indicator": int(picks["model_type"].map(indicator_of).notna().sum()),
               "r2_holdout_median": round(picks["r2_holdout"].median(), 3)}
        for score in ("rmse_drought", "rmse_drought_left_out", "rmse_recovery", "rmse_spring"):
            diff = (picks[score] - reference[score]).dropna()
            row[f"{score}_median_cm"] = round(100 * picks[score].median(), 2)
            better, worse = int((diff < -1e-4).sum()), int((diff > 1e-4).sum())
            row[f"{score}_better"], row[f"{score}_worse"] = better, worse
            row[f"{score}_p"] = round(sign_p(better, worse), 4)
            d_mis = diff[misfit.reindex(diff.index).fillna(False)]
            row[f"{score}_misfit_better"] = int((d_mis < -1e-4).sum())
            row[f"{score}_misfit_worse"] = int((d_mis > 1e-4).sum())
        row["drop_abs_error_median_cm"] = round(100 * picks["drop_error"].abs().median(), 2)
        rows.append(row)
    ref_row = {"indicator": "reference", "passed": int(reference["passed"].sum()),
               "r2_holdout_median": round(reference["r2_holdout"].median(), 3),
               "drop_abs_error_median_cm": round(100 * reference["drop_error"].abs().median(), 2)}
    for score in ("rmse_drought", "rmse_drought_left_out", "rmse_recovery", "rmse_spring"):
        ref_row[f"{score}_median_cm"] = round(100 * reference[score].median(), 2)
    table = pd.concat([pd.DataFrame([ref_row]), pd.DataFrame(rows)], ignore_index=True)
    table.to_csv(out / "drought_scenarios.csv", index=False)
    show = ["indicator", "passed", "gained", "lost", "p_passed", "picks_with_indicator", "r2_holdout_median",
            "rmse_drought_median_cm", "rmse_drought_left_out_median_cm", "rmse_drought_left_out_better",
            "rmse_drought_left_out_worse", "rmse_drought_left_out_misfit_better",
            "rmse_drought_left_out_misfit_worse", "drop_abs_error_median_cm"]
    print(table[show].to_string(index=False))

    if args.extremes:
        scenarios = {"reference": base_types}
        for indicator in args.extremes.split(","):
            scenarios[indicator] = base_types + sorted(
                metrics.loc[metrics["indicator"] == indicator, "model_type"].unique())
        with_extremes(metrics, checks, out, scenarios, args.workers)

    scored = table.dropna(subset=["gained"])
    if scored.empty:
        return
    best = scored.sort_values(["passed", "rmse_drought_left_out_median_cm"], ascending=[False, True]).iloc[0]
    picks = per_series[best["indicator"]]
    detail = pd.DataFrame({
        "series_name": metrics.drop_duplicates("meetreeks_id").set_index("meetreeks_id")["series_name"],
        "polder": where["polder"], "level": where["level"]}).reindex(reference.index)
    detail["misfit"], detail["direction"] = misfit, direction
    for label, frame in (("ref", reference), ("new", picks)):
        for col in ("model_type", "passed", "r2_holdout", *SCORES):
            detail[f"{label}_{col}"] = frame[col]
    detail.reset_index().to_csv(out / "drought_series.csv", index=False)
    print(f"\nbest setting: {best['indicator']} -> drought_series.csv")
    print(detail.groupby(["misfit", "direction"])[["ref_rmse_drought", "new_rmse_drought",
                                                    "ref_rmse_drought_left_out", "new_rmse_drought_left_out"]]
          .median().mul(100).round(2))


if __name__ == "__main__":
    main()
