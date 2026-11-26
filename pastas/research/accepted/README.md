# Research: more accepted Pastas models, same extremes pipeline

Branch `research/accepted-models`. Everything is in this folder, plus one small pipeline change in `tarso_pipeline/simulate.py` (below).

## The result

**60 of 89 validated series pass, against 35 now, with a small menu fixed in advance. With every ongoing variant allowed, 66 pass.** In both cases no series is lost, and no selected model runs away on IMPREX.

> ⚠️ **The return periods of these models are not complete yet.** IMPREX has no Markermeer level, so the extremes stage holds the lake (and air pressure) at its calibration mean. The return periods are therefore **rain-driven, given a normal lake**.
>
> For most series that misses little: the lake moved the head over 8 cm (median) in 2024–2026. But for **22 series** a lasting record lake level would add more than 25 cm, so their wet extremes are too low.
>
> The fix is a synthetic lake level per climate member, from that member's own wind and rain: **[PLAN_synthetic_lake.md](PLAN_synthetic_lake.md)**. Until it is built, read the pass counts as model quality. The return periods hold for the rain-driven part only.

| scenario | model types | passed (green) | gained | lost | p (McNemar) | R² median | holdout R² median | fails rsq / memory / gain / bounds |
|---|---|---|---|---|---|---|---|---|
| **baseline** (the pipeline, refitted) | 5 | **35** | – | – | – | 0.78 | 0.42 | 27 / 42 / 17 / 27 |
| **lean**: + Markermeer level (Edam) and air pressure | 35 | **60** | 25 | 0 | < 0.001 | 0.79 | 0.41 | 17 / 19 / 9 / 9 |
| all ongoing variants | 69 | **66** | 31 | 0 | < 0.001 | 0.81 | 0.43 | 16 / 17 / 11 / 5 |

- **"Passed"** is the pipeline's own stamp: rsq, memory, gain and bounds, with the thresholds unchanged and the dashboard's green.
- **The selection** is `select_models` itself: the passed tier, the holdout guard against Tarso, the lowest AIC with the parameter rule, and the runaway rule on IMPREX extremes. Extremes were simulated for every pick (214 IMPREX runs, all ok), and the selection was repeated until stable.
- **The baseline gives 35, not the committed 36**, because a few review stores have changed since the committed run.
- **The holdout R² of the 25 series that turn green rises** from a median of 0.42 to 0.49, and their R² from 0.76 to 0.79. The gain is not bought with worse prediction.
- **"Lean"** was fixed before the per-series results, to guard against "with 69 variants something passes by chance". It uses only two physical, measured, ongoing stresses, the Markermeer level at Edam and air pressure, on the pipeline's linear types and the bucket. "All ongoing" adds local precipitation and evaporation and the Markermeer-midden level.

### Which factor does what (each factor alone on top of the baseline; no extremes)

From [results/scenarios_without_extremes.csv](results/scenarios_without_extremes.csv):

| factor | passed | gained | p |
|---|---|---|---|
| Markermeer level, RWS Edam, slow response (`wl`) | 48 | +13 | < 0.001 |
| Markermeer level, RWS midden (`wlmid`) | 48 | +13 | < 0.001 |
| `wl` + air pressure, instantaneous lake (`wl1+baro`) | 48 | +13 | < 0.001 |
| Markermeer level, instantaneous (`wl1`) | 45 | +10 | 0.002 |
| bucket + `wl1` + air pressure | 44 | +9 | 0.004 |
| local precipitation, 3 nearest KNMI neerslagstations (`localP`) | 38 | +3 | 0.25 |
| local precipitation and Makkink (`localPE`) | 39 | +4 | 0.13 |
| air pressure alone (`baro`) | 38 | +3 | 0.25 |
| HHNK outer water Drieban (`wlhhnk`) | 39 | +4 | 0.13 |
| soil-moisture bucket alone (`flex`) | 36 | +1 | 1.0 |

### What this says

1. **The Markermeer is the missing driver.** (Correction, 2026-11-23: these are piezometers in the *Schermerboezem* dikes of the Purmer and Beemster, not Markermeer dikes, so the lake works as a regional proxy and the boezem level is the physical boundary still to test; see `docs/experiments/NEXT_STEPS.md`.) The lake level (managed at about −0.40 / −0.20 m NAP, with wind set-up up to +0.43 m in January 2024) explains what P and E cannot. It fixes R² and, above all, memory: a slow P/E response was standing in for the lake.
2. **Air pressure lifts R² a lot but rarely flips a series on its own.**
   - On the smoke series, R² went from 0.71 to 0.90, and the response is about −4 mm of head per hPa (a barometric efficiency of about 40 %). That suggests the heads carry a pressure signal: either the aquifer is confined, or the sensors' barometric compensation is incomplete. **Worth checking with the supplier (Wiertsema):** if it is compensation, it should be fixed in the data, not in the model.
   - It helps most in combination with the lake level.
3. **Local precipitation does not beat Berkhout.**
   - Summed over 616 model pairs, AIC is *worse* with the 3 nearest neerslagstations: a median of +42 per fit. Berkhout is an automatic station with 00–24 sums; the voluntary stations read once a day, 08–08. Their totals are 1–16 % higher.
   - Moving the 08–08 sum one day back fits better than not moving it (summed AIC), so that is the version kept. The few series it flips are not significant.
   - The gridded KNMI products (Rd1, EV24) are interpolated from the same stations, so they are not expected to do better.
4. **The soil-moisture bucket alone adds little.** FlexModel has `lp`, `simax` and `kv` fixed and fits `srmax`, `ks` and `gamma`. Earlier grid tests found the full FlexModel overparameterised, and that holds. Combined with the lake level and air pressure, it is still the pick for 6–8 series (`flex_exp@wl1+baro`).
5. **"Set stressors to their average" (item 3).**
   - For P and E it changes nothing in Pastas: in a linear transfer-function model a stress minus its mean only moves the constant, and Pastas' autoregressive part is the noise model, not the response.
   - For the extra stresses it lets the models be fitted. They enter as anomalies (minus their calibration mean), so the model learns their impulses. On IMPREX they are held at that mean (anomaly 0), so the return periods stay driven by P and E only.
   - **That is also its limit:** the extremes then contain no wind set-up and no wet-winter high water on the lake.
   - Sensitivity: holding the lake at its day-of-year climatology instead changes T100 by a median of 2 mm. The largest changes are 0.17 m (dry) and 0.28 m (wet) ([results/climatology_sensitivity.csv](results/climatology_sensitivity.csv)). A seasonal lake is not the problem; the storms are.
   - **What is left out, per model** ([results/lake_contribution.csv](results/lake_contribution.csv), 65 lean picks with the lake):
     - the lake's contribution to the head ranged over 8 cm (median), 20 cm (90 %) and at most 59 cm in 2024–2026, which is 12 % of the head range (median);
     - at a lasting record level (+0.43 m NAP), 22 models would rise more than 25 cm;
     - a few fitted lake gains are implausible (up to 56 m head per m lake). Holding the lake at its mean hides them; a synthetic lake would expose them to the runaway rule.
   - **How to close the gap:** [PLAN_synthetic_lake.md](PLAN_synthetic_lake.md).
     - A Pastas emulator of the Markermeer level (target level + storage from P − E + wind set-up from the wind stress), calibrated on RWS Edam 2008–now and KNMI Houtribdijk wind.
     - It is driven by each synthetic member's own wind. IMPREX publishes no wind, so the plan is to use the KNMI'23 RACMO wind and pressure (on request from KNMI), or raw IMPREX wind if KNMI has it.
     - The stresses are stored as rows in long warehouse tables, so a series with more stresses (a lake or polder level) is more rows, not more columns.
6. **Return levels mostly stay put.** Lean vs baseline picks, median change in T10 and T100: under 5 mm. The wet T100 moves more than 0.25 m in 12 of 89 series. These are the series whose pick changed most, and they deserve a look in the app.
7. **The HHNK outer-water series** (Drieban 2019–2025) add 4 series, but they ended in October 2025. Unless HHNK publishes them ongoing, the RWS gauges are the usable source.

### What is still yellow (lean: 29 series)
- **memory: 19 series.** These responses are longer than half of a two-year record. They need a longer record; no model change fixes that honestly.
- **rsq: 17 series.**
- **gain: 9 series.**
- **bounds: 9 series.**

## Taking it into the pipeline (a follow-up, not done here)

The full route, including the synthetic lake and the warehouse tables, is in [PLAN_synthetic_lake.md](PLAN_synthetic_lake.md). The steps below are the part that does not depend on it.

1. **Data, ongoing like the KNMI CSV:**
   - RWS Markermeer level at Edam: `ddapi20-waterwebservices.rijkswaterstaat.nl`, daily mean of 10-minute values;
   - KNMI Schiphol (240) `PG` for air pressure.
   - Both are fetched by `stresses.py` (`fetch_rws`, `fetch_knmi`), and the cluster cannot reach them yet, as with KNMI.
2. **Model types in `tarso_pipeline/fit.py`:** add `<type>@wl`, `@wl1`, `@baro`, `@wl+baro`, `@wl1+baro` and the two `flex_*` types as `MODEL_SPECS`, with the stress passed to `build_model`. The checks, selection and dashboard work on them unchanged.
3. **`simulate.py`:** `hold_other_stresses` (in this branch, with tests) already holds every extra stress at its calibration mean on IMPREX. `run_extremes` needs no other change.
4. **Fix before then:** `run_checks` fails on every model under pandas 3 ("assignment destination is read-only" in pastas 1.12's `ps.check`). The root `uv.lock` has pandas 3.1.1, so a local `run_local.py` run writes only `checklist_error` rows. Databricks still runs pandas 2, which is why the committed checks are fine. Pin `pandas<3` for the pastas extra, or upgrade pastas.

## Files

| file | what |
|---|---|
| `stresses.py` | fetch and cache the KNMI neerslagstations (RD), KNMI EV24/PG, the RWS Markermeer level and the HHNK files; inverse-distance weighting per series |
| `variants.py` | the variant registry: `<base>@<factor>[+<factor>]`, every one a Pastas model |
| `run_variants.py` | fit, `extract_metrics`, `run_checks`, holdout and `.pas` for every series and variant → `fit_metrics.csv`, `model_checks.csv` |
| `compare_boezem.py` | the Schermerboezem level (`bz`, `bz1`, `bzn`, `bzd`, Beemster gauge) against the lake and air pressure, one shared set per scenario, per polder → `results/boezem_*.csv`, `picks_<scenario>.csv` |
| `emulate_boezem.py` | a synthetic boezem: a Pastas model of the gauge from KNMI P and E, validated out of sample, then driven by the 16 IMPREX members → `results/boezem_emulator_*` |
| `extremes_boezem.py` | IMPREX return levels with the boezem held at its mean vs the synthetic boezem → `results/extremes_boezem*.csv`. Write-up: `docs/experiments/2026-11-23_schermerboezem.md`. The Dijkgraaf de Boer files it reads were removed on 2026-11-24, so it no longer reruns as is |
| `radar.py` | KNMI gauge-adjusted daily radar (`rad_nl25_rac_mfbs_24h`), one pixel per polder → `data/forcing/local/radar_<polder>.csv` |
| `compare_gemaal.py` | pumping-station levels (`up`, `ip`, `pos`, the `far` station) and radar: passed per polder and cross-section level, the position test, the station test → `results/gemaal_*.csv`, `results/radar_fits.csv`. Write-up: `docs/experiments/2026-11-24_gemaal-peilen.md` |
| `drought.py` | drought indicators from P and E alone: the KNMI potential deficit (`def<D0>`), the rolling E − P (`roll<days>`), a root-zone bucket deficit (`bkt<Smax>l<lp>`); the variant parts of the same names |
| `compare_drought.py` | the drought indicators: like for like against the same model without the term (`results/drought_paired.csv`), the selection per setting (`drought_scenarios.csv`), with IMPREX extremes (`--extremes`, `drought_extremes.csv`), per series (`drought_series.csv`). Write-up: `docs/experiments/2026-11-24_drought.md` |
| `report.py` | the selection per scenario with the pipeline's `select_models`; IMPREX extremes for the picks until stable; counts, McNemar test |
| `results/` | `scenarios.csv` (with extremes), `scenarios_without_extremes.csv` (every factor), the selections, `picks_all_ongoing.csv`, `extremes_summary.csv`, `climatology_sensitivity.csv`, `lake_contribution.csv` |
| `PLAN_synthetic_lake.md` | the plan for return periods with a synthetic Markermeer level, and the warehouse tables for per-series stress sets |

Cached inputs: `pastas/data/forcing/local/` (KNMI) and `pastas/data/stresses/` (RWS).

## Running it

```bash
UV="uv run --no-project --python 3.11 --with pastas==1.12.* --with pandas<3 --with pyyaml --with requests --with scipy"
$UV python -c "import sys; sys.path[:0]=['.','src']; from research.accepted import stresses as s; s.fetch_knmi(); s.fetch_rws('edam'); s.fetch_rws('markermeer.midden')"
$UV python research/accepted/run_variants.py --groups baseline,localP,localP0,localPE,wl,wl1,wlmid,wlhhnk,baro,flex --out local_runs/research/accepted
$UV python research/accepted/run_variants.py --groups wl+baro,wl1+baro,localP+wl,localP+wl+baro,flex@wl+baro,flex@localP+wl --out local_runs/research/accepted
$UV python research/accepted/report.py local_runs/research/accepted --only lean,all_ongoing
$UV --with pytest python -m pytest research/accepted/tests tests/test_simulate.py -q
```

The full fit takes about 20 minutes on 7 workers. The extremes take about 30 minutes.
