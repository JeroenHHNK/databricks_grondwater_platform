# Next steps: more accepted Pastas models

State on 2026-11-24. Background: [2026-11-19_accepted-models.md](2026-11-19_accepted-models.md).

## Where we are
- **In the pipeline since 2026-11-24** (local run with extremes):
  - **62 of 91** validated series pass with the extended model types (lake + pressure, on Berkhout or radar per series, logged), against 38 with the standard five;
  - of the frozen 89: 60 against 36.
- Validated: 89 series, **35–36 pass** with the standard five model types.
- With one shared extra stress set, Markermeer level at Edam + air pressure (`wl1+baro`): **55** (48 with the linear types only, without the bucket versions); with the small `lean` menu (several sets to choose from per series): 60. No runaways on IMPREX, but the return periods hold the lake at its mean (rain-driven only).
- **The Schermerboezem (2026-11-23, Beemster gauge for both polders) does not add accepted models:** boezem + pressure 44, lake + pressure + boezem 54, against 55 for lake + pressure. A synthetic boezem from P/E works on IMPREX, but it moves the return levels by only 0.2 cm (median), 2 cm (90 %). See [2026-11-23_schermerboezem.md](2026-11-23_schermerboezem.md). (The earlier "instroompeil" was not a boezem level: [2026-11-23_purmer-boezem.md](2026-11-23_purmer-boezem.md).)
- **Pumping-station levels (2026-11-24)** do not help either: the crest/toe hypothesis does not hold (Beemster follows the boezem side everywhere; the Purmer polder level fits only through its managed steps, with a negative gain), lake + pressure + `pos` 53. The nearer station's boezem level fits slightly better (p = 0.016, pooled with raw Heerhugowaard), but adds no accepted models. **KNMI radar** (one pixel per polder) helps the Purmer: radar + lake + pressure **56**. See [2026-11-24_gemaal-peilen.md](2026-11-24_gemaal-peilen.md).
- Unvalidated series pass at 2–11 %; validated at 35–42 % (58–75 % with the extra stresses). **Validating more series gives more accepted models than any model change.**

## Steps, in order

1. **Validate Heerhugowaard next** (user to do, in the review app).
   - 71 series, 68 with ≥ 1 year of hourly data, one supplier (Fugro), about 8 km from KNMI Berkhout.
   - Check the first few early: only 1 of the 89 validated series is Fugro data, so how Fugro sensors behave in the models (barometric correction) is unknown.
   - Then **Schermer**: 346 unvalidated series at 150 locations, about 100 with ≥ 1 year; Fugro and Wiertsema. Start with the long ones.

2. ~~Get the boezem level~~ **Done 2026-11-23:** the Schermerboezem at Dijkgraaf de Boer (Beemster side, 2019 → now) and Purmer Zuid (2015–2021, ended). Tested; see above.

3. ~~Choose the one shared stress set~~ **Decided 2026-11-24 (user):**
   - lake level + air pressure (`wl1+baro`) is the stress set;
   - the precipitation source may differ per series (KNMI 249 Berkhout *or* the polder's KNMI radar), as long as it is logged;
   - "We just need as many accepted models as we can."
   - On the research fits that gives **61** (Purmer 37, Beemster 23) against 55.
   - The extra stresses themselves stay one set: no per-series stress combinations.

4. **Ask the suppliers about barometric compensation** (user). Air pressure explains about −4 mm head per hPa. If the sensors are not (fully) compensated, fix it in the data, which helps every model, and the `baro` stress may not be needed.

5. **Pin `pandas<3` for the pastas extra** (agent, small PR to `development`). Without it every local `ps.check` errors.

6. **Into the pipeline**: **built 2026-11-24** on `research/accepted-models` (to go to `development` by PR, never straight to `main`):
   - `tarso_pipeline/fit.py`: `EXTENDED_MODEL_TYPES` (17 types, `*_lake_baro`, `*_lake_baro_radar`, `flex_*`), the default of `scripts/run_local.py` and the fit notebook;
   - `tarso_pipeline/inputs.py`: the forcing and stresses per model type and series, with the polder from `object_data.csv`, and the IMPREX scaling for radar models;
   - data in `pastas/data/forcing/` (`radar_forcing_<polder>.csv`, `stress_lake_edam.csv`, `stress_pressure_240.csv`, built by `scripts/build_forcing_sources.py`); the notebooks fall back to it when the volume lacks a file;
   - every fit row: `precipitation_source`, `evaporation_source`, `extra_stresses`; the app (Pastas-modellen) shows *neerslag*, *verdamping* and *extra stresses*;
   - still open: the long-format stress warehouse (`PLAN_synthetic_lake.md` §Storage). The `.pas` files carry the stress series, so nothing needs it yet.
   - **To do on Databricks:**
     - pull the Git folder;
     - run `fit_pastas_models` with `data_basis=validated` (the default model types are now the extended set);
     - run `run_extremes`;
     - run `export_results`.

7. **Give the extra stress a future for the return periods** (agent, after 3):
   - boezem: **built and tested** (`emulate_boezem.py`, `extremes_boezem.py`, `simulate_member(synthetic=)`); its effect on the return levels is small;
   - lake: needs wind per member, which IMPREX lacks → the KNMI'23 RACMO `uas`/`vas`/`psl` request (`PLAN_synthetic_lake.md` M0–M4).
   - Rewrite `PLAN_synthetic_lake.md` toward one shared set, boezem first (the user has not confirmed this rewrite yet).

8. **Radar forcing:**
   - Built: the monthly IMPREX scaling (`InputLibrary.imprex_factors`, used by `run_extremes` and `run_local`) and the refresh (`scripts/build_forcing_sources.py --fetch`).
   - Refresh monthly. The product lags about a month, and the cluster has no egress, so the refresh runs locally and the files are committed.

9. **Heerhugowaard station test on validated data** (agent, after step 1): rerun `run_variants.py --groups baseline,up,ip,pos,upfar,ipfar,posfar,radar` on the validated Heerhugowaard series; the raw-data run is in the 2026-11-24 log.

10. **Re-fit as records lengthen.** 19 of the 29 still-yellow series fail on memory, which is a record-length problem, not a model problem.

11. **Drought bucket into the pipeline**: **built 2026-11-25** on `research/accepted-models` ([2026-11-24_drought.md](2026-11-24_drought.md)):
    - 12 types `*_lake_baro_bucket(_radar)`, added beside the lake + pressure types (29 in `EXTENDED_MODEL_TYPES`): the selection chooses per series;
    - `tarso_pipeline/inputs.py`: `bucket_deficit` (Smax 150 mm, lp 0.7), the `bucket` stress, `InputLibrary.member_forcing` (radar scaling + the bucket rebuilt per IMPREX member), used by `run_local` and `run_extremes`;
    - `tarso_pipeline/fit.py`: `solve_model`, the nested solve with both gain signs, for the fit and the holdout; `extra_stresses` = `lake,pressure,bucket`;
    - **To do on Databricks:** pull, then `fit_pastas_models`, `run_extremes`, `export_results` (the widget defaults list the 29 types).
12a. **Selection holdout rules and a settings file**: **built 2026-11-26** ([2026-11-26_selection_holdout.md](2026-11-26_selection_holdout.md)): `pastas/config/model_settings.toml` (candidate types, holdout, `min_days`, selection thresholds, bucket), `pastas/config/series_overrides.csv` (per series: exclude stresses or types, extremes not to trust), `run_local.py --reselect`. **To do on Databricks:** pull, rerun `run_extremes` (the selection) and `export_results`; no refit needed.
12. **Abstraction data for the deep Beemster aquifer** (user): the deep filters (−6 m NAP or deeper) drop in a sawtooth during the drought and recover within days. Groundwater sprinkling or other pumping is the likely driver. With permits or pumping records it could become an extra stress.

## Not to pursue (for now)
- Local precipitation stations instead of Berkhout: AIC worse. (Radar, which is gauge-adjusted, does help the Purmer: see step 8.)
- Pumping-station in/uitstroompeil as extra stresses, per position or per nearest station (2026-11-24).
- Bucket (FlexModel) alone: +1 series.
- The KNMI potential precipitation deficit above a threshold as a drought stress: it fits the 2026 drought in-sample and overfits out of sample (2026-11-24).
- Other model families (ARX, trees, LSTM): see [2026-11-18_alt-models.md](2026-11-18_alt-models.md).

## Waiting on the user
- Step 12 (abstraction data for the Beemster).
- Optional: the Purmer peilbesluit (summer/winter target levels by date), to turn the instroompeil into a deviation from target.
- Optional, for the boezem emulator: the set-point regime (target levels by date) and any Dijkgraaf de Boer record before 2019.
- Agreement to rewrite `PLAN_synthetic_lake.md` (step 7).
- Contact with Fugro/Wiertsema (step 4) and with KNMI for the RACMO wind (step 7, only for the lake route).
