"""Turn ``scores.csv`` into the verdict: medians, paired tests, the decision rule, a figure.

    python research/summarise.py local_runs/research/<date>

Adds ``pastas_in_use`` (per series the Pastas type the committed pipeline run selected,
``results_export/valid_pastas/model_selection.csv``) and ``pastas_best_cv`` (per series the
Pastas type with the best mean CV R2: an *optimistic* reference, it picks with the test
blocks). Writes ``summary.md`` and ``delta_holdout.png`` next to the scores.

The decision rule (README): a method is "better" than ``pastas_in_use`` only if
1. its median holdout R2 is at least ``MIN_GAIN`` higher,
2. the paired Wilcoxon signed-rank test on holdout R2 gives p < 0.05 and it wins in more
   than half of the series,
3. its median CV R2 is higher too, and
4. its median in-sample R2 is not lower.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

PASTAS = Path(__file__).resolve().parents[1]
for _path in (PASTAS, PASTAS / "src"):
    sys.path.insert(0, str(_path))

from research.data import model_in_use  # noqa: E402
from research.evaluate import CV_FOLDS  # noqa: E402

BASELINE = "pastas_in_use"
MIN_GAIN = 0.05

# Cross-family ensembles: the mean of the members' simulations, per series and fold.
# ``ens_all`` (every candidate plus the Pastas model in use) is fixed a priori.
# ``ens_families`` takes one member per family; its ARX and tree members were the best of
# their family in the first pass, so it is chosen *after* seeing holdout scores: read it
# as optimistic.
ENSEMBLES = {
    "ens_all": [BASELINE, "dlm_ols", "dlm_sarimax", "arx", "rf", "lgbm", "lgbm_global", "lstm", "cnn"],
    "ens_families": [BASELINE, "dlm_sarimax", "rf", "lstm"],
    "ens_pastas_rf": [BASELINE, "rf"],
    # No tree member: no cap on the extremes, so the one that could run on IMPREX.
    "ens_pastas_sarimax": [BASELINE, "dlm_sarimax"],
}


def ensemble_scores(sims: pd.DataFrame) -> pd.DataFrame:
    """Score rows (as in scores.csv) for the ENSEMBLES, from simulations.parquet."""
    from research import data
    from research.evaluate import make_task, score

    picked = model_in_use()
    in_use = sims[sims["method"] == "pastas_" + sims["meetreeks_id"].map(picked)].assign(method=BASELINE)
    sims = pd.concat([sims, in_use], ignore_index=True)
    precipitation, _ = data.load_forcing()
    series = {s.meetreeks_id: s for s in data.load_series(precipitation)}
    rows = []
    for name, members in ENSEMBLES.items():
        part = sims[sims["method"].isin(members)]
        for (sid, fold), group in part.groupby(["meetreeks_id", "fold"]):
            if sid not in series:
                continue
            table = group.pivot_table(index="datum", columns="method", values="simulated")
            if table.shape[1] < len(members):  # a member failed: no ensemble for this fold
                continue
            rows.append(score(make_task(series[sid], fold), table.mean(axis=1), name, 0.0))
    return pd.DataFrame(rows)


def wide(scores: pd.DataFrame) -> pd.DataFrame:
    """One row per series x method: r2 (full), r2_holdout and r2_cv (mean of the CV folds)."""
    table = scores.pivot_table(index=["meetreeks_id", "method"], columns="fold", values="r2", aggfunc="first")
    out = pd.DataFrame({"r2": table.get("full"), "r2_holdout": table.get("holdout"),
                        "r2_cv": table[[f for f in CV_FOLDS if f in table]].mean(axis=1, skipna=False)})
    return out.reset_index()


def add_references(table: pd.DataFrame) -> pd.DataFrame:
    pastas = table[table["method"].str.startswith("pastas_")].copy()
    pastas["model_type"] = pastas["method"].str[len("pastas_"):]
    picked = model_in_use()
    in_use = pastas[pastas["model_type"] == pastas["meetreeks_id"].map(picked)].assign(method=BASELINE)
    best = (pastas.dropna(subset=["r2_cv"]).sort_values("r2_cv").groupby("meetreeks_id").tail(1)
            .assign(method="pastas_best_cv"))
    return pd.concat([table, in_use.drop(columns="model_type"), best.drop(columns="model_type")], ignore_index=True)


def compare(table: pd.DataFrame) -> pd.DataFrame:
    from scipy.stats import wilcoxon

    base = table[table["method"] == BASELINE].set_index("meetreeks_id")
    rows = []
    for method, group in table.groupby("method"):
        group = group.set_index("meetreeks_id")
        both = group.join(base.add_suffix("_base"), how="inner").dropna(subset=["r2_holdout", "r2_holdout_base"])
        delta = both["r2_holdout"] - both["r2_holdout_base"]
        p = np.nan
        if method != BASELINE and len(delta) >= 10 and (delta != 0).any():
            p = float(wilcoxon(both["r2_holdout"], both["r2_holdout_base"], alternative="greater").pvalue)
        row = {
            "method": method, "n": int(group["r2_holdout"].notna().sum()),
            "r2_median": group["r2"].median(), "r2_holdout_median": group["r2_holdout"].median(),
            "r2_holdout_q25": group["r2_holdout"].quantile(0.25),
            "holdout_negative": int((group["r2_holdout"] < 0).sum()),
            "r2_cv_median": group["r2_cv"].median(),
            "d_holdout_median": delta.median() if len(delta) else np.nan,
            "wins": int((delta > 0).sum()), "paired": len(delta), "p_wilcoxon": p,
        }
        b = base
        row["better"] = bool(
            method != BASELINE and len(delta)
            and row["r2_holdout_median"] >= b["r2_holdout"].median() + MIN_GAIN
            and p < 0.05 and row["wins"] > len(delta) / 2
            and row["r2_cv_median"] > b["r2_cv"].median()
            and row["r2_median"] >= b["r2"].median())
        rows.append(row)
    return pd.DataFrame(rows).sort_values("r2_holdout_median", ascending=False)


def figure(table: pd.DataFrame, path: Path) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    base = table[table["method"] == BASELINE].set_index("meetreeks_id")["r2_holdout"]
    methods = [m for m in table["method"].unique() if m != BASELINE]
    deltas = {m: (table[table["method"] == m].set_index("meetreeks_id")["r2_holdout"] - base).dropna()
              for m in methods}
    order = sorted(deltas, key=lambda m: deltas[m].median() if len(deltas[m]) else -9)
    fig, ax = plt.subplots(figsize=(8, 0.45 * len(order) + 1.5))
    ax.boxplot([deltas[m].clip(-1, 1) for m in order], orientation="horizontal", showfliers=True)
    ax.set_yticks(range(1, len(order) + 1), order)
    ax.axvline(0, color="grey", lw=1)
    ax.set_xlabel(f"holdout R2 minus {BASELINE} (per series, clipped to ±1)")
    fig.tight_layout()
    fig.savefig(path, dpi=130)
    plt.close(fig)


def main() -> None:
    folder = Path(sys.argv[1])
    scores = pd.read_csv(folder / "scores.csv")
    if (folder / "simulations.parquet").exists():
        scores = pd.concat([scores, ensemble_scores(pd.read_parquet(folder / "simulations.parquet"))],
                           ignore_index=True)
    table = add_references(wide(scores))
    result = compare(table)
    figure(table, folder / "delta_holdout.png")
    errors = scores[scores["error"].notna()].groupby(["method", "fold"]).size()
    lines = [f"# Benchmark {folder.name}", "",
             f"{table['meetreeks_id'].nunique()} series. Baseline `{BASELINE}`: the Pastas type the pipeline "
             "selected per series, refitted in this harness on the same data. `pastas_best_cv` picks the best "
             "Pastas type per series with the test blocks: an optimistic reference, not a method. `ens_*` average "
             "the members' simulations (see `ENSEMBLES` in summarise.py; `ens_families` was put together after "
             "a first pass, so it is optimistic).", "",
             result.round(3).to_markdown(index=False), "",
             "![holdout R2 vs baseline](delta_holdout.png)", "",
             "Failed fits (method, fold):", "", (errors.to_frame("n").to_markdown() if len(errors) else "none")]
    (folder / "summary.md").write_text("\n".join(lines), encoding="utf-8")
    print(result.round(3).to_string(index=False))


if __name__ == "__main__":
    main()
