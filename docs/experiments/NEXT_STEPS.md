# Next steps: more accepted Pastas models

State on 2026-11-23. Background: [2026-11-19_accepted-models.md](2026-11-19_accepted-models.md).

## Where we are
- Validated: 89 series, **35–36 pass** today.
- With one shared extra stress set (Markermeer level at Edam + air pressure, `wl1+baro`, or the lake alone, `wl`): **48**; with the small `lean` menu (several sets to choose from per series): 60. No runaways on IMPREX, but the return periods hold the lake at its mean (rain-driven only).
- Unvalidated series pass at 2–11 %; validated at 35–42 % (58–75 % with the extra stresses). **Validating more series gives more accepted models than any model change.**

## Steps, in order

1. **Validate Heerhugowaard next** (user to do, in the review app).
   - 71 series, 68 with ≥ 1 year of hourly data, one supplier (Fugro), about 8 km from KNMI Berkhout.
   - Check the first few early: only 1 of the 89 validated series is Fugro data, so how Fugro sensors behave in the models (barometric correction) is unknown.
   - Then **Schermer**: 346 unvalidated series at 150 locations, about 100 with ≥ 1 year; Fugro and Wiertsema. Start with the long ones.

2. **Get the Schermerboezem level** (user: HHNK gauge, daily, 2015 or earlier to now, ongoing). Put it in `pastas/data/stresses/` like the RWS files and add a loader in `research/accepted/stresses.py` and a `wlboezem` factor in `variants.py`.

3. **Choose the one shared stress set** (agent). Run `baseline`, `@wl1+baro` and `@boezem(+baro)` on the 89 series with `run_variants.py` + `report.py`. Rule: take the boezem if it passes about as many series (within ~3) — it is the physical boundary and easy to give a future; else keep `wl1+baro`. **Never per-series combinations** (user decision).

4. **Ask the suppliers about barometric compensation** (user). Air pressure explains about −4 mm head per hPa. If the sensors are not (fully) compensated, fix it in the data, which helps every model, and the `baro` stress may not be needed.

5. **Pin `pandas<3` for the pastas extra** (agent, small PR to `development`). Without it every local `ps.check` errors.

6. **Into the pipeline** (agent, after 3; PR to `development`, never straight to `main`):
   - `tarso_pipeline/fit.py`: `MODEL_SPECS` entries for the chosen set on the linear types;
   - stress data loaded like the KNMI CSV (cluster has no egress);
   - `simulate.py::hold_other_stresses` is already in, with tests;
   - the warehouse in long format (`stress_catalog`, `stress_observed`, `<prefix>_pastas_model_stresses` with the `calibration_mean`), see `PLAN_synthetic_lake.md` §Storage.

7. **Give the extra stress a future for the return periods** (agent, after 3):
   - boezem: hold at the target level, or a small Pastas emulator from P − E per IMPREX member (no wind needed);
   - lake: needs wind per member, which IMPREX lacks → the KNMI'23 RACMO `uas`/`vas`/`psl` request (`PLAN_synthetic_lake.md` M0–M4).
   - Rewrite `PLAN_synthetic_lake.md` toward one shared set, boezem first (the user has not confirmed this rewrite yet).

8. **Re-fit as records lengthen.** 19 of the 29 still-yellow series fail on memory, which is a record-length problem, not a model problem.

## Not to pursue (for now)
- Local precipitation stations instead of Berkhout: AIC worse.
- Bucket (FlexModel) alone: +1 series.
- Other model families (ARX, trees, LSTM): see [2026-11-18_alt-models.md](2026-11-18_alt-models.md).

## Waiting on the user
- The boezem level series (step 2).
- Agreement to rewrite `PLAN_synthetic_lake.md` (step 7).
- Contact with Fugro/Wiertsema (step 4) and with KNMI for the RACMO wind (step 7, only for the lake route).
