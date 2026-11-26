# Plan: return periods with a synthetic Markermeer level (option 4)

**Status:** plan, not built. Branch `research/accepted-models`.

## Why

- **The gain:** the models that pass more checks use the Markermeer level at Edam (and air pressure) as extra stresses.
- **The gap:** the IMPREX members have no lake level, so `simulate_member` now holds it at its calibration mean. The return periods are then **rain-driven, given a normal lake**.
- **How much that leaves out,** for 65 of the 89 lean picks (`results/lake_contribution.csv`):
  - the lake moved the head over a range of 8 cm (median) to 59 cm (max) in 2024–2026;
  - for **22 series**, a lasting record lake level would add more than 25 cm. Their wet extremes are too low as long as the lake is held.
- **The aim:** a lake level that belongs with each synthetic member, so each member drives the groundwater models with a consistent set of stresses (P, E, lake, pressure). The return periods then include wind set-up and wet-winter high water.

Holding the lake at its mean stays as the fallback and as a sensitivity run.

## The idea in one line

Lake level = target level + storage (slow, from net rain) + wind set-up (fast, from wind stress).

- **A lake emulator** is calibrated on observations: RWS Edam 2008–now and KNMI wind on the lake.
- **It is then driven by each synthetic member's own P, E and wind.**
- **The emulator is itself a Pastas model,** so it reuses the pipeline's tooling: fit, checks, holdout, simulate.

## What exists, what does not (checked 2026-11-19)

| need | source | status |
|---|---|---|
| observed lake level | RWS `WATHTE` Edam, 10-minute, `ddapi20-waterwebservices.rijkswaterstaat.nl` | ✅ **2008–now**; 2015–now cached (`data/stresses/rws_edam_daily.csv`) |
| observed wind on the lake | KNMI 258 Houtribdijk `FG`, `DDVEC`, `FHX` (daily) | ✅ 2006–now. Berkhout 249 has wind from 1999, Schiphol 240 from 1990 |
| observed P, E, pressure | KNMI 249 (P, E), 240 (`PG`) | ✅ cached |
| synthetic P, E | IMPREX nlgrid, 16 members, 1951–2000 (704 years after spin-up) | ✅ in use (bias-adjusted `pr`, `evappot`) |
| **synthetic wind** for the IMPREX members | — | ❌ **not published.** IMPREX carries only bias-adjusted `pr`, `tas` and `evappot` |
| **synthetic wind** for KNMI'23 | KNMI'23 RACMO v2.3, *user-friendly* v3.0: `uas`, `vas`, `psl` (daily), 8 members × 30 years per period | ⚠️ in the dataset, **but not on the portal** ("not bias-corrected"): **request from KNMI** |
| official lake statistics | WBI2023 / Hydra-NL Markermeer water-level statistics | ⚠️ held by RWS / HHNK (dike assessment): **ask HHNK** |

### The decision about the synthetic set (milestone 0)

- **A. KNMI'23 RACMO, 8 × 30 years per period, P/E/wind/psl from the same run.**
  - The reference period 1991–2020 is today's climate: 240 years.
  - The scenarios (Ld/Ln/Md/Mn/Hd/Hn, 2050/2100/2150) give future return periods, as the old `pastas_machine` RACMO work already did for P/E.
  - Its 1991–2020 overlaps the wind observations (2006–2020), so the wind can be bias-corrected properly.
  - Downside: 240 years instead of 704, so T > 100 rests more on the GPD extrapolation.
- **B. Raw RACMO wind and `psl` for the 16 IMPREX members (1951–2000), if KNMI can supply them.** This keeps the 704 years and the current P/E.
  - Downside: no overlap with Houtribdijk (2006+). The wind correction would have to assume a stationary climate and map against Schiphol 1990–2000.
- **The choice:** ask KNMI for both. Take **A** if B is not available or cannot be corrected well. If B is available, run both and compare T10/T100 on the lean picks.
- **No switch** is needed for the models: they keep calibrating on observed data. Only the extremes stage reads the synthetic set.

## Milestones

### M0: data (about 1 week, mostly waiting)
- **Request from KNMI:** the KNMI'23 v3.0 `uas`/`vas`/`psl` for the grid cells over the Markermeer and the series, the reference period and the scenarios. Also the IMPREX raw wind if it exists.
- **Ask HHNK** for the WBI2023 / Hydra-NL Markermeer level statistics, and for any polder-level gauges per `peilgebied_code` (see storage).
- **Fetch** RWS Edam 2008–2014 to extend the cache, and KNMI 258 `FG`/`DDVEC`/`FHX` 2006–now. Both are already supported by `stresses.py` (add `FG:DDVEC` to `fetch_knmi`).

### M1: lake emulator (go/no-go)
- **The Pastas model of the daily lake level at Edam:**
  - the **target level** from the managed regime (about −0.40 m NAP winter, −0.20 m summer, with a flexible summer level since 2018), as a day-of-year step stress or taken from the data;
  - **storage:** `RechargeModel(P, E)` with an Exponential response, the slow rise in wet winters when the lake cannot drain;
  - **wind set-up:** set-up ∝ U²·cos(θ − θ₀). In two linear stresses that is `StressModel(u·|U|, One)` and `StressModel(v·|U|, One)`, the wind stress components from `FG` and `DDVEC`. The fit finds the set-up direction θ₀ for Edam itself.
- **Fit and validation split:** calibrate 2008–2019, validate 2020–2026, so the validation contains the January 2024 record (+0.43 m).
- **Go if all of these hold:**
  - validation R² ≥ 0.7;
  - the annual maxima within 10 cm (median absolute error);
  - January 2024 within 15 cm;
  - when driven by the reference-period synthetic forcing, the emulated annual-maximum distribution matches the observed one (QQ) and the WBI2023 / Hydra-NL statistics at T10.
- **No-go:** fall back to option 3 (the official lake statistics combined with the rain-driven head extremes), or to the bootstrap of observed lake years.
- **Known limits:**
  - daily means damp hourly set-up peaks. That is consistent with the daily groundwater models, but should be checked against `FHX`;
  - the management (sluice and pump capacity, the flexible summer level) is assumed stationary;
  - a linear wind stress cannot invent a record set-up beyond its physics.

### M2: synthetic wind and pressure
- **Wind:** quantile mapping of the member wind speed per direction sector (8 sectors) onto Houtribdijk, over the overlap of option A or on Schiphol for option B. The direction is kept.
- **Pressure:** `psl` mapped onto Schiphol `PG` by mean and variance per month.
- **Check:** the emulator driven by synthetic forcing reproduces the observed lake climatology (monthly mean and standard deviation, annual-maximum distribution).

### M3: a synthetic lake per member
- **Output:** the emulator run per member gives a lake series per member, and the pressure series comes from `psl`.
- **The reference must match.** Every groundwater model was fitted on *its own* anomaly (the stress minus its mean over that series' calibration days). The synthetic series is shifted by that same stored mean, so the lake held at its mean still gives exactly today's result. This is a test.

### M4: groundwater extremes with every stress
- **`tarso_pipeline/simulate.py`:** `simulate_member` takes a dict of synthetic stresses by stress model name (`level`, `baro`) and replaces them. `hold_other_stresses` stays as the fallback, for when a stress has no synthetic counterpart, and as the sensitivity run.
- **`run_extremes`:** reads the synthetic stresses per member. Otherwise it is unchanged.
- **The runaway rule now bites for lake models:** a model with an implausible lake gain (up to 56 m head per m lake in the current fits) runs away and is not picked.
- **Report** per series: the rain-only (held) and joint (synthetic lake) T10/T100, and the lake's share of each return level.

### M5: pass rate (the goal)
- **Selection:** rerun it with the joint extremes. The lean scenario now has 60 passing; expect a few runaways to drop out.
- **Then raise the count:**
  - test the lake response forms per series (`One` / `Exponential` / an Exponential with a bounded `a`);
  - add polder-level stresses where HHNK has gauges (by `peilgebied_code`). A managed polder level has a known target regime, so it has a future by construction;
  - re-fit as records lengthen: 19 of the 29 still-yellow series fail on memory, which is a record-length problem.
- **The headline number:** passing *with* joint extremes and no runaway.

### M6: pipeline and warehouse (see storage)
- **Model types in `fit.py`:**
  - `MODEL_SPECS` entries for `@wl`, `@wl1`, `@baro`, `@wl+baro`, `@wl1+baro` and `flex_*`;
  - `build_model` takes stresses from the store, not from fixed files.
- **Notebooks:** they load the stress tables, and the emulator runs as a step before `run_extremes`.
- **Dashboard:** the pages show the stress set per series and the lake's share of the return level.

## Storage in the data warehouse

**The principle: a stress is a row, not a column.** Which stresses a series' model uses is data, not schema.

The series do not all have the same stresses:
- a Purmer series may get a lake level, air pressure and a polder level;
- a Beemster series may get only P and E.

A wide table would mean a column per stress, mostly NULL, and a schema change for every new stress. In long tables, a new stress or a series with an extra feature is new rows.

Tables in the output schema (`waterkeringbeheer_prd.jvanruitenbeek` until the team schema exists; `gold_prd` stays read-only). They take the `unvalid_` / `valid_` prefix where they depend on the data basis.

| table | grain | columns |
|---|---|---|
| `stress_catalog` | per stress | `stress_id` (e.g. `knmi_249_rh`, `knmi_240_pg`, `rws_edam_wathte`, `knmi_258_wind`, `hhnk_<peilgebied>_peil`), `kind` (`precipitation` / `evaporation` / `lake_level` / `air_pressure` / `wind_u` / `wind_v` / `polder_level`), `source`, `location_code`, `lat`, `lon`, `unit`, `ongoing`, `first_date`, `last_date`, `refreshed_at` |
| `stress_observed` | stress × day | `stress_id`, `date`, `value`; partitioned by `kind`. Fed the way the KNMI CSV is now (fetched outside, committed or copied to the volume, loaded by a notebook), because the cluster has no egress |
| `stress_synthetic` | ensemble × member × stress × day | `ensemble` (`imprex`, `knmi23_ref`, `knmi23_Hd_2050`, …), `member`, `stress_id`, `date`, `value`, `version` (e.g. the emulator version for the lake); partitioned by `ensemble`, `member` |
| `<prefix>_pastas_model_stresses` | series × model type × stress model | `meetreeks_id`, `model_type`, `stressmodel` (`Recharge`, `level`, `baro`), `stress_id`, `rfunc`, `calibration_mean` (the anomaly reference, needed in M3), `synthetic_stress_id`, `hold_mode` |
| `lake_emulator_runs` | per emulator version | `version`, the calibration and validation periods, R², the annual-maximum error, the January 2024 error, `parameters` JSON |

- **`fit_metrics` gets one more column,** `stresses` (a JSON list of `stress_id`). The `model_type` naming (`linear_gamma@wl+baro`) already tells the dashboard which family it is.
- **Migration:**
  - `knmi_forcing_249.csv` becomes `stress_observed` rows for `knmi_249_rh` / `knmi_249_ev24`;
  - the IMPREX member CSVs become `stress_synthetic` rows with `ensemble = imprex`.
  - The loaders (`load_forcing_pair`, `load_member`) get table-backed versions with the same return values, so nothing downstream changes.

## Open questions
1. What does "the Purmer now contains additional features compared to the Beemster data" refer to exactly? Extra measured series in the warehouse (e.g. polder or ditch levels) for the Purmer only? Or the per-series stress sets above? The storage design covers both, but it decides which `polder_level` stresses M5 can use.
2. Who requests the KNMI'23 wind/psl from KNMI, and are the WBI2023 / Hydra-NL Markermeer statistics available at HHNK?
3. Option A (KNMI'23, 240 years now + future scenarios) or B (IMPREX, 704 years) as the default extremes set, if both turn out to be possible?
