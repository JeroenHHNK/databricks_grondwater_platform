# 2026-11-18 · Other model families vs Pastas (R² and holdout R²)

**Branch:** `research/alt-models` (commit `cb03b5e`). **Code and full write-up:** [`pastas/research/README.md`](../../pastas/research/README.md), [`pastas/research/results/summary.md`](../../pastas/research/results/summary.md), per-series scores in `pastas/research/results/scores.csv`.

## Question
With the same inputs (KNMI 249 P and E), do ARX/SARIMAX, tree ensembles or deep learning give a higher R² **and** a higher holdout R² than the Pastas model the pipeline selects? Any candidate must be able to *simulate* (no measured heads), because the extremes stage drives it with 704 IMPREX years.

## Set-up
- 89 validated series, cleaned with `clean_head`; the holdout split and R² of `tarso_pipeline.fit.holdout_r2`.
- Folds: full, holdout (70/30), cv50, cv60. CV R² = mean of cv50, cv60, holdout.
- Global models (LightGBM global, LSTM, CNN) cut training at one calendar date, so no series learns its test months from another.
- A fairness test changes every head after the split and checks the prediction does not move (`research/tests/test_fairness.py`).
- **Decision rule, fixed beforehand:** median holdout R² ≥ +0.05, Wilcoxon p < 0.05 and > half the series won, higher median CV R², in-sample R² not lower.

## Results
| method | R² | holdout R² median | holdout < 0 | wins / 89 | p |
|---|---|---|---|---|---|
| pastas_in_use (baseline) | 0.78 | 0.41 | 23 | – | – |
| ens_pastas_sarimax | 0.81 | 0.45 | 17 | 53 | 0.003 |
| ens_pastas_rf | 0.90 | 0.46 | 15 | 47 | 0.07 |
| dlm_sarimax | 0.80 | 0.44 | 21 | 42 | 0.49 |
| rf | 0.97 | 0.44 | 22 | 34 | 0.91 |
| lstm / cnn | 0.69 / 0.66 | 0.18 / 0.10 | 30 / 32 | 24 / 14 | 1.0 |

## Conclusions
1. **No method passes the rule.** `ens_pastas_sarimax` comes closest (significant, wins in 53 series) but gains +0.04 in the median, not +0.05. It was composed after a first pass, so it is optimistic.
2. Trees overfit two years of data (R² 0.97, holdout no better); LSTM/CNN have too little data.
3. Ensembles mostly lift the worst quarter (−0.09 → 0.14–0.22), not the median.
4. A forest cannot extrapolate, so it would damp IMPREX extremes. Only the SARIMAX ensemble is a candidate for the extremes.

## Why this was not pursued
The user's real goal turned out to be **more accepted models** (the `passed` stamp), not a higher holdout R². That led to the next experiment, [2026-11-19_accepted-models.md](2026-11-19_accepted-models.md), which changes the stresses of the Pastas models instead of the model family.

## Open
- Confirm `ens_pastas_sarimax` on unseen data (unvalidated series or a later year) with members fixed beforehand, then an IMPREX runaway check. Low priority.

## Reproduce
```bash
cd pastas
UV="uv run --no-project --python 3.11 --with pastas==1.12.* --with statsmodels --with scikit-learn \
    --with lightgbm==4.6.* --with torch --with pyarrow --with scipy --with pyyaml --with tabulate"
$UV python research/run_benchmark.py --methods pastas,arx,trees --out local_runs/research/full
$UV python research/run_benchmark.py --methods lgbm_global,cnn,lstm --out local_runs/research/full
$UV python research/summarise.py local_runs/research/full
```
About 1–2 hours on 8 cores (the deep models dominate).
