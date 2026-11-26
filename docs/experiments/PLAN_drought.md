> **Status 2026-11-24: steps 1–7 done.** Results: [2026-11-24_drought.md](2026-11-24_drought.md). The winner is `bkt150l7`; step 8 (the pipeline) waits on the user.

# Research plan: drought response (deficit indicator / soil-moisture bucket), with return periods from IMPREX

Saved on execution as `docs/experiments/PLAN_drought.md`. Results are logged in `docs/experiments/2026-11-xx_drought.md`.

## Context: what the example shows
**BE_0072_BUKR_PB2** (MSLV8_o54294):
- filter −7 to −8 m NAP;
- picked model `linear_dexp_lake_baro_radar`: passed, R² 0.71, holdout R² 0.48;
- record 2024-10-31 → 2026-10-26.

| | Jun 2026 | Jul 2026 | Aug 2026 | Sep 2026 |
|---|---|---|---|---|
| P radar / E Makkink (mm) | 85 / 113 | **5 / 114** | 88 / 96 | 105 / 54 |
| mean residual obs − sim (m) | +0.08 | **−0.08** | −0.05 | +0.06 |

- **Observed:** a steady fall from −4.20 to −4.58 m through July, then a rise of 0.25 m within days of about 25 mm of rain (16–20 Aug).
- **The model:** −4.30 → −4.41, a smooth and too-weak response in both directions.
- **Gemaal levels and the lake did not drive it:** the polder level held at −5.0 and the boezem at −0.47 the whole summer; the lake fell while the head recovered.
- **The same cross-section, Jun → end of Jul:**

| piezometer | filter | observed drop | simulated drop |
|---|---|---|---|
| BUKR PB2 (crest, deep) | −7 m | **−0.38 m** | −0.11 m |
| BIT PB2 (toe, deep) | −7.2 m | **−0.39 m** | −0.11 m |
| BUKR PB1 (crest, shallow) | −3.4 m | −0.13 m | −0.09 m |

- **The deep aquifer drops three times more than the shallow filter above it.** Soil moisture alone would damp out with depth, so a regional or abstraction effect is likely. For example, groundwater sprinkling that starts with the deficit and stops when it rains would explain the fast recovery.
- **All 89 picks, residual 10 Jul–15 Aug 2026 in residual-SD units:**
  - **14 series** < −1 SD: the model stays too high, as here;
  - **7 series** > +1 SD: the model drops too far, the classic linear over-application of evaporation that a bucket with ET reduction fixes.
  - So the fix must allow both directions.
- **4 Aug 2026 (−4.764 m)** is a one-day dip and the series minimum, so it drives `dry_T100`. **The user confirmed it as a real observation (2026-11-24).** It stays in the data and counts as the drought's real low. A sudden one-day dip in the deep aquifer, in the driest week, fits a short abstraction (e.g. sprinkling) better than soil moisture.

**On "autoregressive":** Pastas' deterministic part is not autoregressive. It is a linear convolution of P and E (and lake, pressure) with impulse responses. The AR part is only a noise model, and our fits run without one (`noise_model=none`). The real limit is **linearity**: one gain per mm of recharge, the same in a wet January as in a dry July, with a fixed memory. So a 5-week rain-free spell gives the same drop per mm as any other period, and the fit compromises with the winter. That is why a deficit-dependent term can help.

## Goal and rules
- **Goal:** better simulation of drought extremes (and the recovery after them) on the 21 drought-misfit series, without losing passed models.
- **The return periods must keep working:**
  - every new input must be computable from **P and E alone**, so it can be rebuilt for each IMPREX member;
  - IMPREX has only `precipitation_mm` and `makkink_mm` per member, and the radar types already get scaled members.
- The checks, the thresholds and `select_models` stay unchanged.
- As before, every variant is a Pastas model.
- **Few extra parameters, chosen globally:**
  - each series has about 2 years with one strong drought (2026), plus a dry spring (Mar–May 2025: P 53 mm against E 237 mm);
  - indicator settings such as the threshold and window are chosen on a global grid by summed AIC, not per series, so a drought term cannot be fitted to one event.

## Candidate models (in order of cost)
**D1: potential precipitation deficit as an extra stress** (the drought indicator)
- `D(t)` = the KNMI definition: cumulative E − P from 1 April, never below 0, reset each year.
- The stress is `max(0, D − D0)`, with D0 on a global grid of 0, 50, 100, 150 mm. It is zero in winter, so it acts only in droughts.
- Built as `ps.StressModel(deficit_excess, ps.Exponential, settings="evap")`. That adds 2 parameters, and the gain sign is free, so both directions are allowed.
- Variant **D1b**: rolling P − E over 30, 60 or 90 days (no reset, like SPEI), clipped to its dry side.
- Added to the best linear types of the extended set: `linear_{dexp,gamma,fourparam}_lake_baro(_radar)`.

**D2: soil-moisture bucket as a derived stress**
- A simple root-zone bucket run on the same P and E:
  - storage `S ≤ Smax`;
  - actual ET = E·min(1, S/(lp·Smax));
  - overflow = recharge.
- Its **deficit `Smax − S`** becomes the extra stress, as in D1.
- Smax (50, 100, 150, 250 mm) and lp (0.5, 0.7) are on a global grid.
- This also covers the "model drops too far" group: the deficit term can counteract the linear E response once the soil is dry.

**D3: the Pastas-native bucket, revisited**
- `ps.rch.FlexModel` is in the extended set, but here it fits clearly worse (R² 0.58 against 0.72; holdout 0.27 against 0.48).
- To test:
  - `kv` free instead of fixed at 1;
  - `gw_uptake=True`;
  - srmax on the grid as initial value;
  - `ps.rch.Berendrecht`.
- No extra stress: the bucket replaces the linear recharge, and `simulate_member` already supports it.
- It is kept only if it beats D1/D2 on the drought metrics.

**Not proposed:**
- A per-series threshold or window.
- Temperature-driven terms: Makkink E already carries temperature and radiation.
- ML models (an earlier test found them not better).

## Return periods
- **Synthetic per member:**
  - D1 and D2 are deterministic functions of P and E (`tarso_pipeline/inputs.py::drought_indicator(precipitation, evaporation, kind, **params)`);
  - `run_extremes` computes them from each scaled IMPREX member and passes them through the existing hook `simulate_member(..., synthetic={name: series})` (`tarso_pipeline/simulate.py:143`), so they are recomputed, not held at a mean;
  - lake and pressure keep being held.
- **Calibration** uses the same function on the model's own forcing, station or radar, so calibration and simulation are consistent.
- **Checks on the return periods:**
  - the runaway rule as before;
  - plausibility of the new `dry_T100` against the observed 2026 minimum and the driest IMPREX years;
  - a deeper dry T100 is expected.
- **KNMI'23 scenarios:**
  - IMPREX stays the basis, because 16 × 50 years is needed for T100, and it is bias-corrected and already in the pipeline;
  - KNMI'23 adds no variable this model needs;
  - it is a later **climate sensitivity**: transform the IMPREX members with the KNMI'23 monthly P and E changes (dry scenario `Hd`, 2050), recompute the indicator and compare T10 and T100;
  - that is a phase 3 option and not needed to answer this question.

## Evaluation
- **Out-of-sample by design:** the existing holdout (fit on the first 70 %, score the last 30 %, `fit.py::holdout_r2`) covers about Mar–Oct 2026, so it already contains the drought.
- **Drought metrics** per series: RMSE over 1 Jul–15 Aug 2026; error of the minimum (observed against simulated drop from June to the end of July); recovery error 16 Aug–30 Sep.
- **Leave-the-drought-out:** refit with Jun–Sep 2026 masked, then predict it. This is the strictest test that a drought term generalises.
- **Second event:** spring 2025.
- **A variant counts as an improvement only if all of these hold:**
  - the drought RMSE improves on most of the 21 series, by a sign test;
  - the passed count does not fall, by McNemar on flips;
  - the median holdout R² of the picks is not lower;
  - there are no new runaways.

## Order of work
1. **Diagnostics:**
   - the drought residuals of all 91 picks, depth against drop (deep against shallow filters), per polder;
   - which series drop too far and which too little;
   - a short notebook section in `notebooks/exploration/`.
2. **Indicators:** `drought_indicator` with unit tests (reset on 1 April, floor 0, bucket mass balance); the grids for D1, D1b and D2.
3. **Research fits** via the existing harness:
   - new parts `def`, `defroll` and `bucket` in `research/accepted/variants.py`;
   - `run_variants.py --raw-polder`, `compare_gemaal.py`-style comparison;
   - choose D0, window, Smax and lp globally by summed AIC.
4. **D3:** FlexModel and Berendrecht variants.
5. **Leave-the-drought-out** refits and the 2025 check.
6. **Extremes:** in `research/`, the IMPREX members through `simulate_member(synthetic=...)` for the winning variant; runaway and T100 plausibility.
7. **Write-up** in `docs/experiments/`; update `NEXT_STEPS.md`.
8. **Pipeline,** only if it wins:
   - a `ModelSpec.derived` field in `fit.py`;
   - `inputs.py` builds the indicator;
   - `run_extremes` and `run_local` pass it as `synthetic`;
   - `extra_stresses` logs it (e.g. `lake,pressure,deficit`), so the app shows it automatically;
   - then a user decision and a PR to `development`.

## Files
- **New:** `pastas/research/accepted/drought.py` (the indicators, later moved to `tarso_pipeline/inputs.py`), tests in `research/accepted/tests/test_inputs.py`.
- **Extend:** `research/accepted/variants.py` and `run_variants.py`, plus a comparison script modelled on `research/accepted/compare_gemaal.py`.
- **Reuse:**
  - `tarso_pipeline.selection.select_models`, `model_checks.run_checks`, `fit.holdout_r2`;
  - `simulate.simulate_member(synthetic=)`, `inputs.scale_members`.

## Open input from you (not blocking)
- Is there groundwater abstraction or sprinkling from groundwater near BE0072 or in the Beemster? Was there a sprinkling ban in July 2026? If so, it supports the deficit hypothesis for the deep filters.
- ~~The 4 Aug 2026 spike at BE_0072_BUKR_PB2~~: **answered 2026-11-24, a real observation.**

## Verification
- Unit tests for the indicators:
  - D = 0 on 1 April;
  - D never negative;
  - the bucket closes its water balance;
  - the IMPREX member indicator equals the calibration function on the same input.
- Baseline reproduction: `run_variants` without the drought parts gives the current 62 passed.
- `simulate_member` with a synthetic deficit: the head matches a hand convolution on a toy model.
- The repo suites (`scripts/run_tests.py`) stay green.
- Nothing goes into the pipeline, and nothing is committed or pushed, without your go-ahead.
