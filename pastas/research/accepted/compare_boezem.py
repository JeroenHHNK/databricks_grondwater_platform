"""Does the Schermerboezem level help? Passed counts per polder for one shared stress set.

    python research/accepted/compare_boezem.py local_runs/research/accepted_bz

Each scenario is the pipeline's five types plus *one* variant group (the same stress set for
every series), selected with ``select_models`` without extremes, as in
``results/scenarios_without_extremes.csv``. Also compares the fits directly: per series, the
best AIC and R² of a group's models against the baseline's and the lake's.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

PASTAS = Path(__file__).resolve().parents[2]
for path in (PASTAS, PASTAS / "src"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from research.accepted import stresses  # noqa: E402
from research.accepted.report import group_of, mcnemar_p, select  # noqa: E402

GROUPS = ("wl", "wl1+baro", "baro", "bz", "bz1", "bzn", "bzd", "bz+baro", "bz1+baro", "wl1+baro+bz", "wl1+baro+bz1")


def main() -> None:
    out = Path(sys.argv[1])
    metrics = pd.read_csv(out / "fit_metrics.csv")
    checks = pd.read_csv(out / "model_checks.csv")
    metrics["group"] = metrics["model_type"].map(group_of)
    polder = stresses.polders()
    base_types = sorted(metrics.loc[metrics["group"] == "baseline", "model_type"].unique())

    picks = {}
    for name in ("baseline", *GROUPS):
        allowed = base_types + ([] if name == "baseline" else
                                sorted(metrics.loc[metrics["group"].isin([name, f"flex@{name}"]), "model_type"].unique()))
        chosen = select(metrics, checks, None, allowed)
        picks[name] = chosen[chosen["selected"]].set_index("meetreeks_id")
        # The picks with their .pas path, for extremes_boezem.py (e.g. picks_bz1_baro.csv).
        picks[name].reset_index().merge(metrics[["meetreeks_id", "model_type", "model_path"]],
                                        on=["meetreeks_id", "model_type"]).to_csv(
            out / f"picks_{name.replace('+', '_')}.csv", index=False)

    base = picks["baseline"]["passed"]
    rows = []
    for name, chosen in picks.items():
        passed = chosen["passed"].reindex(base.index).fillna(False).astype(bool)
        where = passed.index.map(lambda i: polder.get(i, "other"))
        gained, lost = int((passed & ~base).sum()), int((~passed & base).sum())
        row = {"scenario": name, "passed": int(passed.sum()), "gained": gained, "lost": lost,
               "p": round(mcnemar_p(gained, lost), 4)}
        for p in ("Purmer", "Beemster"):
            row[f"{p}_passed"] = int(passed[where == p].sum())
            row[f"{p}_r2_holdout_median"] = round(chosen.loc[passed.index[where == p], "r2_holdout"].median(), 3)
        rows.append(row)
    table = pd.DataFrame(rows)

    # Direct fit comparison: best model per series and group (linear + flex types).
    ok = metrics[metrics["success"].astype(bool)]
    best = ok.groupby(["meetreeks_id", "group"]).agg(aic=("aic", "min"), r2=("r2", "max"),
                                                      r2_holdout=("r2_holdout", "max")).reset_index()
    wide = best.pivot(index="meetreeks_id", columns="group")
    fit_rows = []
    for g in GROUPS:
        for ref in ("baseline", "wl"):
            d_aic = wide[("aic", g)] - wide[("aic", ref)]
            for p in ("Purmer", "Beemster"):
                mask = d_aic.index.map(lambda i: polder.get(i) == p)
                fit_rows.append({"group": g, "vs": ref, "polder": p,
                                 "median_d_aic": round(d_aic[mask].median(), 1),
                                 "share_better_aic": round((d_aic[mask] < -2).mean(), 2),
                                 "median_d_r2": round((wide[("r2", g)] - wide[("r2", ref)])[mask].median(), 3)})
    fits = pd.DataFrame(fit_rows)

    table.to_csv(out / "boezem_scenarios.csv", index=False)
    fits.to_csv(out / "boezem_fits.csv", index=False)
    with pd.option_context("display.width", 220):
        print(table.to_string(index=False))
        print()
        print(fits.to_string(index=False))


if __name__ == "__main__":
    main()
