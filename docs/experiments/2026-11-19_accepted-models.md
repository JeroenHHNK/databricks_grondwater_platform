# 2026-11-19 → 23 · More accepted Pastas models (yellow → green)

**Branch:** `research/accepted-models`. **Code and full write-up:** [`pastas/research/accepted/README.md`](../../pastas/research/accepted/README.md); result tables in `pastas/research/accepted/results/`; the synthetic-lake plan in [`PLAN_synthetic_lake.md`](../../pastas/research/accepted/PLAN_synthetic_lake.md).

## Question (the user's words, shortened)
> We have 36 accepted models and 53 that need review. Crank those up. You may change the model architecture, not the core of the pipeline; we still need return periods / groundwater extremes. Ideas: (1) other or local P/E and new stressors available past *and* future, (2) soil-moisture buckets, (3) set stressors to their average so only their impulses register. Report how many models improve.

## Set-up
- Checks, thresholds and `select_models` unchanged. Every variant is a Pastas model, fitted like `tarso_pipeline.fit.build_model` without a noise model, so AIC is comparable.
- Variant names `<base>@<factor>[+<factor>]` (`research/accepted/variants.py`). Factors:
  - `localP` / `localP0` / `localPE`: 3 nearest KNMI neerslagstations (RD, 08–08, shifted −1 day / unshifted), plus Makkink from 249/240;
  - `wl` (RWS Edam lake level, Exponential), `wl1` (Edam, `One`), `wlmid` (RWS Markermeer midden), `wlhhnk` (HHNK Drieban outer water, ended Oct 2025);
  - `baro` (KNMI 240 air pressure, `One`);
  - `flex_exp` / `flex_gamma`: FlexModel bucket with `lp`, `simax`, `kv` fixed.
- Extra stresses enter as **anomalies** (minus their calibration mean). On IMPREX they are **held at that mean** (`tarso_pipeline/simulate.py::hold_other_stresses`, new, tested), so the extremes stay P/E-driven.
- Scenarios: `baseline`, `+<factor>` each, `lean` (fixed beforehand: wl, wl1, baro, wl+baro, wl1+baro and their flex versions), `all_ongoing`. IMPREX extremes run for every pick until the selection is stable (214 runs), so the runaway rule applies.
- Significance: McNemar on passes gained vs lost.

## Results
| scenario | passed / 89 | gained | lost | p |
|---|---|---|---|---|
| baseline (refitted; committed run says 36) | 35 | – | – | – |
| one shared set: base types + `@wl1+baro` (no extremes) | 48 | +13 | 0 | < 0.001 |
| one shared set: base types + `@wl` (no extremes) | 48 | +13 | 0 | < 0.001 |
| **lean** menu | **60** | +25 | 0 | < 0.001 |
| all ongoing | 66 | +31 | 0 | < 0.001 |

- No runaway in any IMPREX run. The holdout R² of the series that turned green rose (median 0.42 → 0.49).
- Single factors: lake level +13, air pressure +3 (but R² 0.71 → 0.90 on the smoke series), local precipitation +3 (AIC *worse*, median +42 per fit), bucket +1.
- Every single-factor count: `results/scenarios_without_extremes.csv`. A greedy combination of variant groups (computed in the session, not saved as a CSV) went 36 → 54 → 60 → 63 → 65 (+wl1+baro incl. its flex versions, +wlmid, +wl, +wl+baro), but stacking groups means per-series stress sets, which the user ruled out. **The number for "one shared set" is 48.**
- Per polder (validated, now → lean): Purmer 20/48 (42 %) → 36 (75 %); Beemster 14/40 (35 %) → 23 (58 %).
- Unvalidated pass rates per project: Schermer 4 % (347 series), Heerhugowaard 6 % (71), Beemster 11 %, Purmer 2 %. **Validation is the biggest lever.**
- Still yellow in lean (29): memory 19 (record too short), rsq 17, gain 9, bounds 9.

## The catch: return periods
IMPREX has no lake level or air pressure, so they are held at their mean. The return periods are then **rain-driven, given a normal lake**.
- The lake contributed 8 cm (median) to 59 cm (max) to the head in 2024–2026.
- For 22 models a lasting record lake level would add > 25 cm, so their wet extremes are too low.
- Some fitted lake gains are implausible (up to 56 m head per m lake). Holding the lake at its mean hides them from the runaway rule.
- Holding at a day-of-year climatology instead of the mean: median 2 mm difference, max 0.28 m.

## Decisions and corrections from the user (important for whoever continues)
1. **Keep the system simple.** No per-series stress combinations. At most **one extra stress set, the same for every series**. (I first misread this as a question about per-polder features.)
2. **"We want the climate set that gives the most accepted models."** Note: the climate set (IMPREX vs KNMI'23) does *not* change acceptance; it only enters through the runaway rule. Acceptance comes from the calibration stresses.
3. **These are Schermerboezem dikes, not Markermeer dikes.** The earlier README text "dike piezometers along the Markermeer" was wrong; the lake level is a regional proxy (wind, wet winters), and the *boezem* level is the physically right boundary.
4. **Boezem level:** the user asked whether a heavily managed boezem adds more noise than signal. Answer: the checks reject a stress that only adds noise; a managed level is easy to give a future (its target level, or an emulator from P − E), so it has fewer IMPREX problems than the lake. **Data not yet available**: not in `gold_prd.hl_grondwaterpeilmeting`; the user was asked for the HHNK Schermerboezem gauge series.
5. **Synthetic lake (plan option 4)** was planned (`PLAN_synthetic_lake.md`), but that plan still assumes per-series stress sets and the lake as the main driver. Rewrite it toward *one shared set, boezem first* before building.

## Pipeline change on this branch
- `pastas/src/tarso_pipeline/simulate.py`: `HOLD_MODES`, `hold_other_stresses`, `simulate_member(..., hold="mean")`. Tests in `pastas/tests/test_simulate.py`. Without extra stresses it changes nothing.

## Data cached in git (the cluster has no internet)
- `pastas/data/forcing/local/knmi_rd_stations.csv`, `knmi_auto_stations.csv`;
- `pastas/data/stresses/rws_edam_daily.csv`, `rws_markermeer.midden_daily.csv` (2015–now).

## Reproduce
```bash
cd pastas
UV="uv run --no-project --python 3.11 --with pastas==1.12.* --with pandas<3 --with pyyaml --with requests --with scipy"
$UV python research/accepted/run_variants.py --groups baseline,localP,localP0,localPE,wl,wl1,wlmid,wlhhnk,baro,flex --out local_runs/research/accepted
$UV python research/accepted/run_variants.py --groups wl+baro,wl1+baro,localP+wl,localP+wl+baro,flex@wl+baro,flex@localP+wl --out local_runs/research/accepted
$UV python research/accepted/report.py local_runs/research/accepted --only lean,all_ongoing
$UV --with pytest python -m pytest research/accepted/tests tests/test_simulate.py -q
```
Fits about 20 min on 7 workers, extremes about 30 min. `local_runs/` is git-ignored; the result CSVs that matter are copied to `research/accepted/results/`.
