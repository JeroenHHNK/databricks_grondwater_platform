# 2026-11-26 — Selection: a slightly lower R² for a better holdout; one settings file

Branch `research/accepted-models`. Run: `pastas/local_runs/valid_pastas/run_local_bucket_26_11_25` (run `20261125T162334Z`, 183 review stores incl. Heerhugowaard, 29 model types, IMPREX extremes).

## 1. Question
After the bucket run, the median holdout R² of the Beemster seemed to fall from 0.59 to 0.47. Why? And is a model with a slightly lower R² but a better holdout R² the better pick, as long as R² stays acceptable?

## 2. Why the Beemster "fell"
**Mostly a mixed-up comparison.** The 0.59 (24 Nov) was the median over the *passed* models, the 0.47 (25 Nov) over *all* picks. Like for like, on the same data:

| Beemster | 24 Nov | 25 Nov |
|---|---|---|
| passed models: median R² / holdout | 0.85 / 0.59 | 0.88 / 0.62 |
| all 40 picks: median R² / holdout | 0.75 / 0.48 | 0.82 / 0.47 |

- The data are the same: a model type fitted in both runs has an identical holdout.
- No passed model was lost, and 2 were gained.

**What is real:** over all picks Beemster stays flat while the Purmer rises (0.45 → 0.56).
- 30 of the 40 Beemster picks changed, mostly to a bucket model; gains and losses cancel out.
- **The cause:** the pick is the lowest AIC. The bucket model beats the same model without it on AIC in 94–96 % of the pairs, but on holdout in only 60–63 % (median +0.03 to +0.05).
- **The old holdout guard is relative to Tarso** (drop if more than 0.05 below Tarso's holdout). Where Tarso itself predicts poorly, nearly anything passes.
- **Examples** (R² / holdout):

| series | picked | holdout better by |
|---|---|---|
| `c2d4ccd1` | `flex_exp_lake_baro_bucket_radar` 0.77 / **0.02** | `flex_gamma_lake_baro_radar` 0.71 / 0.51 |
| `7a2fa683` | `linear_dexp_lake_baro_bucket_radar` 0.91 / 0.72 | `linear_dexp_lake_baro_radar` 0.90 / 0.85 |
| `f1c0fbd0` | `linear_fourparam_lake_baro_bucket_radar` 0.88 / 0.39 | `flex_gamma_lake_baro_radar` 0.86 / 0.50 |

In all three the better option loses 0.01–0.07 R² and gains 0.11–0.49 holdout, with fewer parameters. So the answer to the question is yes: for these the slightly lower R² is the better model.

## 3. Set-up
New optional rules in `tarso_pipeline/selection.py::_holdout_rules`, applied in this order after the passed tier, each only while at least one candidate is left:
- **a.** (existing) holdout more than `holdout_vs_tarso` (0.05) below Tarso's;
- **R.** R² more than `max_r2_loss` below the best R² left. This guard keeps b–d from buying a better holdout with a model that no longer fits. It comes *after* a, so the new rules only narrow what the Tarso rule keeps;
- **b.** a bucket model with holdout more than `holdout_vs_without_bucket` below the same model without the bucket;
- **c.** holdout more than `holdout_gap` below the best holdout left;
- **d.** holdout below `min_holdout`.

**Decision rule, fixed before choosing:**
- the passed count must not fall;
- no changed pick may get a worse holdout;
- median R² of the picks may fall by at most about 0.01;
- among those, take the highest median and lowest-quarter holdout.

**Replay:** `select_models(..., rules=...)` on the run's fit_metrics, model_checks and extremes summary. With the old rules it reproduces the run's picks exactly (0 changes).

## 4. Results
All 160 picks (passed is 122 in every variant: the rules only act within a tier):

| rule | changed | bucket picks | median R² | median holdout | holdout p25 |
|---|---|---|---|---|---|
| old (a only) | 0 | 121 | 0.861 | 0.571 | 0.151 |
| b 0.00 | 27 | 103 | 0.861 | 0.579 | 0.209 |
| c 0.10 | 39 | 109 | 0.858 | 0.613 | 0.310 |
| d 0.30 | 9 | 119 | 0.861 | 0.579 | 0.310 |
| b 0.05 + c 0.10 | 41 | 107 | 0.858 | 0.613 | 0.313 |
| **b 0.05 + c 0.10 + R 0.10** | 37 | 106 | 0.859 | 0.609 | 0.313 |
| b 0.05 + c 0.10 + R 0.05 | 37 | 114 | 0.859 | 0.601 | 0.302 |

- **c alone did most of the work, but it gave up the fit in a few best-effort series.** For example, `80759a95` went from R² 0.89 to 0.05 for a holdout that stays useless (−2.97 → −1.70). Guard R fixes that.
- **First attempt, R before a: wrong.** In `c5b1f46c` every model predicts hopelessly (holdout −7.8 to −15). R removed Tarso (low R²), and a could then no longer act, so the pick moved from Tarso (−7.8) to a model at −12.6. Moving R after a fixed it. A test covers it (`test_the_r2_guard_does_not_undo_the_tarso_rule`).

**Chosen: b 0.05 + c 0.10 + R 0.10**, in `pastas/config/model_settings.toml`.

Final, after the order fix (`run_local.py --reselect`):

| | passed | median R², all picks | median holdout, all picks | holdout p25 | median holdout, passed | bucket picks |
|---|---|---|---|---|---|---|
| Beemster (40) | 26 → 26 | 0.82 → 0.81 | 0.47 → **0.50** | 0.04 → 0.27 | 0.62 → **0.72** | 30 → 21 |
| Purmer (48) | 38 → 38 | 0.87 → 0.86 | 0.56 → **0.59** | 0.17 → 0.27 | 0.58 → 0.62 | 36 → 32 |
| Heerhugowaard (60) | 47 → 47 | 0.88 → 0.88 | 0.70 → 0.72 | 0.39 → 0.47 | 0.74 → 0.74 | 47 → 45 |
| All (160) | 122 → 122 | 0.86 → 0.86 | 0.57 → **0.61** | 0.15 → **0.31** | 0.68 → 0.70 | 121 → 106 |

- **37 picks changed, all with a better holdout** (median +0.26, worst +0.007). R² fell by a median 0.019 and at most 0.095.
- **The three examples now pick the better option** from section 2.
- **Return periods:** every model already had its extremes, and none of the new picks runs away.

## 5. The settings file and the per-series exceptions
**`pastas/config/model_settings.toml`** (`tarso_pipeline/settings.py`):
- `[fit]`: the candidate model types, holdout, `min_days`, noise.
- `[selection]`: the thresholds above, plus `aic_tolerance` and `runaway_margin_m`.
- `[bucket]`: Smax and lp.
- **Defaults equal the old constants**, so a missing file or key changes nothing. A typo in a key or section is an error, not a silent default.
- **Read by** `run_local.py`, `fit_pastas_models` (blank widgets), `run_extremes` (the selection) and `export_results`.
- `tomllib`, so no new dependency.

**`pastas/config/series_overrides.csv`** (`tarso_pipeline/overrides.py`), one row per series:
- `exclude_stresses` (`lake`, `pressure`, `bucket`, `radar`) and `exclude_model_types`: those models are not fitted and never picked;
- `trust_extremes = nee`: the pick's reason gets "extremen niet betrouwbaar: \<note\>";
- `note`, `added_by`, `added_at`.
- It starts empty. A wrong row stops the run, naming the row.
- It is not the person's model choice (`model_choice/`), which still wins over both.

**Not done:**
- per-series model settings (bucket size, response function per series). With about 2 years and one drought per series they would fit that one event;
- showing the `trust_extremes` warning anywhere in the app other than the reason.

## 6. Reproduce
```bash
cd pastas
uv run --no-project --python 3.11 --with "pastas==1.12.*" --with "pandas<3" --with h5py --with scipy \
  --with matplotlib --with pyarrow --with numba python scripts/run_local.py --reselect run_local_bucket_26_11_25 --export
```
The grid replay was a scratch script around `select_models(fits, checks, extremes, rules=SelectionSettings(...))`. It is not kept in the repo; the table above is the result.

## 7. Caveats and open points
- **The holdout is only the last ~7 months of about 2 years**, so a holdout difference of 0.1 is partly noise. The rules are global, which keeps that noise from being tuned per series.
- **Research reruns change.** `research/accepted/report.py` calls `select_models` with the repo settings, so a rerun of earlier experiments now uses the new rules, and their logged numbers came from the old ones.
- **The bucket still wins 106 of 160 picks.** It is not a loss where it predicts as well; the rules only stop it where it predicts clearly worse.
- **On Databricks:** pull, rerun `run_extremes` (or only its selection cell) and `export_results`. No refit is needed.
