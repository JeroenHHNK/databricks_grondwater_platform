"""Pumping-station levels and radar: accepted models, the position hypothesis, the station test.

    python research/accepted/compare_gemaal.py local_runs/research/gemaal

1. **Scenarios** (as ``compare_boezem``): the pipeline's five types plus one variant group,
   the same stress set for every series, selected with ``select_models`` without extremes.
   Passed counts per polder and per cross-section level (crest / berm+toe).
2. **Position hypothesis**: per series the best AIC over the linear + bucket types with the
   uitstroompeil (``up``) against the instroompeil (``ip``). Crest series should prefer the
   uitstroompeil, berm/toe series the instroompeil. Also the fitted gain of each.
3. **Station test** (two stations per polder): per series the best AIC with the nearest
   station against the other one, for ``up``, ``ip`` and ``pos``, against the distance
   difference.

4. **Radar**: per series the best model with radar precipitation against Berkhout.

Writes ``gemaal_scenarios.csv``, ``gemaal_position.csv``, ``gemaal_stations.csv``, ``radar_fits.csv`` and the
picks ``picks_<scenario>.csv`` into the run folder.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

PASTAS = Path(__file__).resolve().parents[2]
for path in (PASTAS, PASTAS / "src"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from research.accepted import stresses  # noqa: E402
from research.accepted.report import group_of, mcnemar_p, select  # noqa: E402

GROUPS = ("wl1+baro", "up", "ip", "up+ip", "pos", "posx", "pos+baro", "wl1+baro+pos",
          "upfar", "ipfar", "posfar", "radar", "radar+wl1+baro")
GAIN = {"up": "uitstroom_d", "ip": "instroom_d", "pos": "gemaal_d",
        "upfar": "uitstroom_d", "ipfar": "instroom_d", "posfar": "gemaal_d"}


def scenario_table(metrics, checks, where, out: Path) -> pd.DataFrame:
    base_types = sorted(metrics.loc[metrics["group"] == "baseline", "model_type"].unique())
    picks = {}
    for name in ("baseline", *GROUPS):
        extra = [] if name == "baseline" else sorted(
            metrics.loc[metrics["group"].isin([name, f"flex@{name}"]), "model_type"].unique())
        if name != "baseline" and not extra:
            continue  # not run (yet)
        chosen = select(metrics, checks, None, base_types + extra)
        picks[name] = chosen[chosen["selected"]].set_index("meetreeks_id")
        picks[name].reset_index().merge(metrics[["meetreeks_id", "model_type", "model_path"]],
                                        on=["meetreeks_id", "model_type"]).to_csv(
            out / f"picks_{name.replace('+', '_')}.csv", index=False)
    base = picks["baseline"]["passed"]
    polder = where["polder"].reindex(base.index)
    level = where["level"].reindex(base.index)
    present = [p for p in ("Purmer", "Beemster", "Heerhugowaard") if (polder == p).any()]
    rows = []
    for name, chosen in picks.items():
        passed = chosen["passed"].reindex(base.index).fillna(False).astype(bool)
        gained, lost = int((passed & ~base).sum()), int((~passed & base).sum())
        row = {"scenario": name, "passed": int(passed.sum()), "gained": gained, "lost": lost,
               "p": round(mcnemar_p(gained, lost), 4)}
        for p in present:
            for lv in ("upper", "lower"):
                row[f"{p}_{lv}"] = int(passed[(polder == p) & (level == lv)].sum())
            row[f"{p}_passed"] = int(passed[polder == p].sum())
            row[f"{p}_r2_holdout_median"] = round(chosen["r2_holdout"].reindex(base.index)[polder == p].median(), 3)
        rows.append(row)
    table = pd.DataFrame(rows)
    totals = {"scenario": "series", "passed": len(base)}
    for p in present:
        for lv in ("upper", "lower"):
            totals[f"{p}_{lv}"] = int(((polder == p) & (level == lv)).sum())
        totals[f"{p}_passed"] = int((polder == p).sum())
    return pd.concat([table, pd.DataFrame([totals])], ignore_index=True)


def best_per_group(metrics: pd.DataFrame, tarso: bool = False) -> pd.DataFrame:
    """Per series and group (linear + flex together): the lowest-AIC model's AIC, R2 and parameters.

    Without Tarso by default: it cannot carry an extra stress, so a baseline that includes it
    would compare different model families, not the effect of the stress."""
    ok = metrics[metrics["success"].astype(bool)].copy()
    if not tarso:
        ok = ok[~ok["model_type"].str.startswith("tarso")]
    ok["family"] = ok["group"].str.replace("flex@", "", regex=False).replace({"flex": "baseline"})
    ok = ok.sort_values("aic")
    return ok.groupby(["meetreeks_id", "family"]).first().reset_index()


def position_test(best: pd.DataFrame, where: pd.DataFrame) -> pd.DataFrame:
    wide = best.pivot(index="meetreeks_id", columns="family", values="aic")
    r2 = best.pivot(index="meetreeks_id", columns="family", values="r2")
    params = best.set_index(["meetreeks_id", "family"])["parameters"]
    rows = []
    for meetreeks_id in wide.index:
        if not {"up", "ip", "baseline"} <= set(wide.columns) or pd.isna(wide.loc[meetreeks_id, ["up", "ip"]]).any():
            continue
        row = {"meetreeks_id": meetreeks_id, "polder": where["polder"].get(meetreeks_id),
               "pos": where["pos"].get(meetreeks_id), "level": where["level"].get(meetreeks_id),
               "d_aic_up_minus_ip": wide.loc[meetreeks_id, "up"] - wide.loc[meetreeks_id, "ip"],
               "d_aic_up_vs_base": wide.loc[meetreeks_id, "up"] - wide.loc[meetreeks_id, "baseline"],
               "d_aic_ip_vs_base": wide.loc[meetreeks_id, "ip"] - wide.loc[meetreeks_id, "baseline"],
               "d_r2_up_vs_base": r2.loc[meetreeks_id, "up"] - r2.loc[meetreeks_id, "baseline"],
               "d_r2_ip_vs_base": r2.loc[meetreeks_id, "ip"] - r2.loc[meetreeks_id, "baseline"]}
        for fam in ("up", "ip"):
            p = json.loads(params.get((meetreeks_id, fam), "{}") or "{}")
            row[f"gain_{fam}"] = p.get(GAIN[fam])
        rows.append(row)
    return pd.DataFrame(rows)


def summarise_position(table: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for (polder, level), g in table.groupby(["polder", "level"]):
        d = g["d_aic_up_minus_ip"]
        rows.append({"polder": polder, "level": level, "series": len(g),
                     "prefer_up (dAIC<-2)": int((d < -2).sum()), "prefer_ip (dAIC>2)": int((d > 2).sum()),
                     "sign test p": round(mcnemar_p(int((d < -2).sum()), int((d > 2).sum())), 3),
                     "median dAIC up-ip": round(d.median(), 1),
                     "median dAIC up vs base": round(g["d_aic_up_vs_base"].median(), 1),
                     "median dAIC ip vs base": round(g["d_aic_ip_vs_base"].median(), 1),
                     "median dR2 up": round(g["d_r2_up_vs_base"].median(), 3),
                     "median dR2 ip": round(g["d_r2_ip_vs_base"].median(), 3),
                     "median gain up": round(g["gain_up"].median(), 2), "median gain ip": round(g["gain_ip"].median(), 2)})
    return pd.DataFrame(rows)


def station_test(best: pd.DataFrame, where: pd.DataFrame) -> pd.DataFrame:
    wide = best.pivot(index="meetreeks_id", columns="family", values="aic")
    rows = []
    for meetreeks_id in wide.index:
        w = where.loc[meetreeks_id] if meetreeks_id in where.index else None
        if w is None or w["polder"] not in ("Beemster", "Heerhugowaard"):
            continue
        stations = stresses.stations_by_distance(w["lat"], w["lon"], w["polder"])
        row = {"meetreeks_id": meetreeks_id, "polder": w["polder"], "pos": w["pos"], "level": w["level"],
               "near": stations[0][0], "km_near": round(stations[0][1], 2), "far": stations[1][0],
               "km_far": round(stations[1][1], 2)}
        for kind in ("up", "ip", "pos"):
            if kind in wide.columns and f"{kind}far" in wide.columns:
                row[f"d_aic_{kind}_near_minus_far"] = wide.loc[meetreeks_id, kind] - wide.loc[meetreeks_id, f"{kind}far"]
        rows.append(row)
    return pd.DataFrame(rows)


def summarise_stations(table: pd.DataFrame) -> pd.DataFrame:
    rows = []
    table = table.assign(km_gap=table["km_far"] - table["km_near"])
    for kind in ("up", "ip", "pos"):
        col = f"d_aic_{kind}_near_minus_far"
        if col not in table:
            continue
        for polder, g in table.dropna(subset=[col]).groupby("polder"):
            d = g[col]
            rows.append({"stress": kind, "polder": polder, "series": len(g),
                         "near better (dAIC<-2)": int((d < -2).sum()), "far better (dAIC>2)": int((d > 2).sum()),
                         "sign test p": round(mcnemar_p(int((d < -2).sum()), int((d > 2).sum())), 3),
                         "median dAIC near-far": round(d.median(), 1),
                         "spearman(dAIC, km gap)": round(g[[col, "km_gap"]].corr(method="spearman").iloc[0, 1], 2)})
    return pd.DataFrame(rows)


def radar_test(best: pd.DataFrame, where: pd.DataFrame) -> pd.DataFrame:
    """Per series: the best model with radar precipitation against the best with Berkhout."""
    if "radar" not in set(best["family"]):
        return pd.DataFrame()
    wide = {k: best.pivot(index="meetreeks_id", columns="family", values=k) for k in ("aic", "r2", "r2_holdout")}
    both = wide["aic"][["baseline", "radar"]].dropna().index
    return pd.DataFrame({"meetreeks_id": both, "polder": [where["polder"].get(i) for i in both],
                         "d_aic": (wide["aic"]["radar"] - wide["aic"]["baseline"]).loc[both].to_numpy(),
                         "d_r2": (wide["r2"]["radar"] - wide["r2"]["baseline"]).loc[both].to_numpy(),
                         "d_r2_holdout": (wide["r2_holdout"]["radar"] - wide["r2_holdout"]["baseline"]).loc[both].to_numpy()})


def main() -> None:
    out = Path(sys.argv[1])
    metrics = pd.read_csv(out / "fit_metrics.csv")
    checks = pd.read_csv(out / "model_checks.csv")
    metrics["group"] = metrics["model_type"].map(group_of)
    where = stresses.positions()

    scenarios = scenario_table(metrics, checks, where, out)
    best = best_per_group(metrics)
    position = position_test(best, where)
    stations = station_test(best, where)
    scenarios.to_csv(out / "gemaal_scenarios.csv", index=False)
    position.to_csv(out / "gemaal_position.csv", index=False)
    stations.to_csv(out / "gemaal_stations.csv", index=False)
    summarise_position(position).to_csv(out / "gemaal_position_summary.csv", index=False)
    summarise_stations(stations).to_csv(out / "gemaal_stations_summary.csv", index=False)
    radar_fits = radar_test(best_per_group(metrics, tarso=True), where)
    if not radar_fits.empty:
        radar_fits.to_csv(out / "radar_fits.csv", index=False)
    with pd.option_context("display.width", 250, "display.max_columns", 40):
        print(scenarios.to_string(index=False))
        print()
        print(summarise_position(position).to_string(index=False))
        print()
        print(summarise_stations(stations).to_string(index=False))
        if not radar_fits.empty:
            print()
            print(radar_fits.groupby("polder")[["d_aic", "d_r2", "d_r2_holdout"]].median().round(3))


if __name__ == "__main__":
    main()
