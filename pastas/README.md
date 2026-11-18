# Pastas pipeline: groundwater models and return levels

For every groundwater series we:
1. Fit time-series models (Pastas) that explain the measured head from precipitation and evaporation.
2. Check whether each model is credible.
3. Pick one model per series.
4. Drive that model with a long synthetic climate to estimate how high or low the head gets once in T years (return levels, e.g. T = 10, 100, 1000).

The Boxplots cross sections and the app show those return levels. The monitoring dashboard shows which series have a usable model.

Everything runs on Databricks:
- **Code:** read from this Git folder.
- **Source:** read-only, from `gold_prd`.
- **Results:** written to our own schema `waterkeringbeheer_prd.jvanruitenbeek`. Model files go to the `misc_files` volume.

```
meting (raw) or validated_meting (reviewed)        KNMI 249 Berkhout, daily P and Makkink E
        │                                                       │
        ▼                                                       ▼
 build_manifest ──► fit_pastas_models: 5 model types per series, checks, holdout R²
                            │  .pas files, fit_metrics, model_checks, simulations
                            ▼
                    run_extremes: each model × 16 IMPREX climate members → ~704 synthetic years
                            │  extremes, return levels, model_selection (the pick)
                            ▼
        app: pick family / overrule model ──► load_family_choice ──► views *_return_levels_chosen, *_model_in_use
                            ▼
        export_results (git copy for the app)      monitoring dashboard (Pastas page)
```

## Which data

### Heads (`data_basis`)

Every notebook has a `data_basis` widget. Everything it reads and writes follows from it (`tarso_pipeline.paths`).

| `data_basis` | reads | tables | artefacts on the volume |
|---|---|---|---|
| `unvalidated` (default) | `gold_prd.hl_grondwaterpeilmeting.meting`: every row with a value, whatever its `validatie_status` | `unvalid_pastas_<kind>` | `…/pastas_pipeline/unvalid_pastas/run_yy_mm_dd/` |
| `validated` | `waterkeringbeheer_prd.jvanruitenbeek.validated_meting`: only the hours approved in the data validation review, with corrections applied (see [`data_validation/README.md`](../data_validation/README.md)) | `valid_pastas_<kind>` | `…/pastas_pipeline/valid_pastas/run_yy_mm_dd/` |

- **Same shape:** both sets of tables are identical in shape, so the two bases compare row for row (app: *Pastas → Gevalideerd vs ongevalideerd*).
- **Cleaning:** duplicate timestamps are averaged and the series is resampled to daily means (`clean.py`).
- **Minimum length:** a series must span at least `min_days` = 365 days and have `min_obs` = 100 daily values. A Tarso fit needs a full seasonal cycle.

### KNMI forcing: calibration

The calibration uses the daily values of **KNMI station 249 Berkhout** from the KNMI *daggegevens* product:
- `RH`: the daily precipitation sum;
- `EV24`: the Makkink reference evaporation.

Both are published in 0.1 mm and converted to mm (`RH = −1`, "< 0.05 mm", becomes 0).

Handling in the fit:
- **Units:** the fit notebook converts them to **m/day** (`forcing_units=m`), the unit of the head. The unit is recorded on every fit.
- **Warmup:** 3650 days before the first head, to spin up the model's memory. Station 249 starts in 1999 and the heads in 2022, so the full warmup fits. Station 235 De Kooy (from 1970) is there for older series.
- **Publication lag:** KNMI publishes 1–2 days behind. The calibration end is trimmed back to the last forcing day, and the trim is recorded (`calibration_trimmed`, `days_trimmed_from_end`).

The cluster has no internet access, so the forcing is fetched locally and committed (`data/forcing/knmi_forcing_249.csv`, refreshed by `scripts/check_knmi.py`). It is then copied to `/Volumes/waterkeringbeheer_prd/misc_files/misc_files/pastas_pipeline/knmi_forcing/`. See [Refreshing the forcing](#refreshing-the-knmi-forcing).

### IMPREX ensemble: extremes

The return levels are not extrapolated from the few years of measurements. Each calibrated model is instead driven by the **bias-corrected IMPREX ensemble**:
- **Source:** 16 members of the KNMI-RACMO-ECEARTH historical run, 1951–2000, for the Berkhout grid cell.
- **Columns:** daily `precipitation_mm` and `makkink_mm`.
- **Bias correction:** already applied; do not apply another.

Turning the simulations into extremes:
- **Blocks:** per member the first 5 years are dropped as spin-up. The annual extremes are taken per hydrological year (April–March), and partial years are dropped.
- **Pooling:** the extremes are pooled across members, giving **704 synthetic years** per tail (16 × 44).
- **What a return level means:** it describes how the model responds to a 50-year climate sampled 16 times. It is not a measurement.

## Which models

Every series is fitted with five model types (`tarso_pipeline.fit.MODEL_SPECS`):

| model type | recharge | response | parameters |
|---|---|---|---|
| `tarso` | Tarso: two drainage levels, a threshold model, the series' own range as `dmin`/`dmax` | exponential per level | 6 (A0, a0, d0, A1, a1, d1), no constant |
| `linear_exp` | linear, `R = P − f·E` | Exponential (A, a) | 3 (+ constant) |
| `linear_gamma` | linear | Gamma (A, n, a) | 4 (+ constant) |
| `linear_dexp` | linear | DoubleExponential (A, α, a1, a2) | 5 (+ constant) |
| `linear_fourparam` | linear | FourParam (A, n, a, b) | 5 (+ constant) |

Why these five:
- **Grid test:** a test on the validated series compared Tarso, Linear, Berendrecht, Peterson and FlexModel × four responses. It left these five.
- **Dropped:**
  - Berendrecht hit its parameter bounds on every series and ran away in the extremes.
  - FlexModel fitted worse.
  - Peterson won on AIC but predicted unseen data worse. `peterson_gamma` can still be fitted on request.
- **Noise model:** none by default (`noise=none`). An AR(1) or ARMA(1,1) noise model did not make the runs test pass and lowered the R² of the simulation.
- **Holdout:** each fit also gets a second fit on the first 70 % of the observations, scored on the last 30 % with the forcing only (`r2_holdout`). That shows what the model does on data it has not seen.

## The checks, and when a model passes

The checks follow **Brakenhoff et al. (2022)**, *"Application of time series analysis to estimate drawdown from multiple well fields"*. The paper proposes a checklist for whether a Pastas model is credible enough to use.

They are implemented with pastas' own check functions (`tarso_pipeline.model_checks`) and written per fit, one row per check, to `<prefix>_model_checks`:

| check | `check` label starts with | passes when | why |
|---|---|---|---|
| **R²** (`pass_rsq`) | `rsq` | R² ≥ 0.7 | the model explains most of the variation |
| **Response memory** (`pass_memory`) | `t95` | t95, the time in which 95 % of the response to a rain pulse has passed, < half the calibration period. For Tarso both drainage levels | a memory longer than the data cannot be identified from it |
| **Gain** (`pass_gain`) | `\|` | \|A\| > 1.96 σ: the gain differs significantly from zero | the stress actually does something |
| **Parameter bounds** (`pass_bounds`) | `Bounds` | no optimal parameter sits on its lower or upper bound | a parameter on its bound means the optimum lies outside the allowed range |
| Runs test (`pass_runs`) | `runs` | residuals not autocorrelated (p ≥ 0.05) | **reported only**: without a noise model, daily residuals are always autocorrelated |

A checklist that errors becomes one `checklist_error` row and passes nothing.

### The pick per series (`run_extremes` → `<prefix>_model_selection`)

1. **Candidates:** the fits that succeeded and do **not run away**. Running away means a T=100 level more than **1.5 m** beyond the measured range of the series (`eligible`, `runaway`).
2. **"Passed" stamp:** R², memory, gain *and* bounds all pass (`passed`). If any candidate has the stamp, only those compete (`tier = passed`); otherwise all candidates do (`tier = best_effort`).
3. **Holdout:** drop a candidate whose holdout R² is more than 0.05 below Tarso's. A lower AIC bought with worse prediction is overfitting.
4. **AIC:** the lowest AIC wins, but a model with fewer parameters within 2 AIC of the lowest wins over it.

The reason is in `reason`, in Dutch, for every model type.

A person can overrule the pick in the app (*Pastas → Modellen → Modelvergelijking en keuze*). The pick is saved to `model_choice/<prefix>_model_choice.csv` and loaded by `load_family_choice`. The view `<prefix>_model_in_use` then gives per series the model that applies: `bron = handmatig` (picked by a person) or `automatisch`.

### When a model is up for review (monitoring dashboard)

The Pastas page of the monitoring dashboard ([`databricks_dashboard/README.md`](../databricks_dashboard/README.md)) colours each series by its **model in use on the validated data**:

| colour | rule | action |
|---|---|---|
| gray | no validated data or no validated fit | review the data first, then run with `data_basis=validated` |
| red | the fit failed, the model runs away in the extremes, or no model type is eligible | not usable; look at the data and the fit |
| **yellow** | has an R², but fails R² ≥ 0.7, the response memory or the parameter bounds | **up for review**: compare the model types in the app, overrule the pick or accept it with a note |
| green | passes R², memory and bounds | usable |

The map leaves the gain out of its colour. A green series can still fail the gain check, and then its automatic pick is `best_effort`; the dashboard's model table shows `pass_gain`.

## Return levels

| T | level |
|---|---|
| ≤ 100 | **empirical**, from the 704 pooled synthetic years (~7 exceedances at T=100); no distribution needed |
| 250–500 | the **picked family** above the **picked threshold**: `gpd` (the tail flattens off) or `exp` (it does not) |
| 1000 | the same, but extrapolation whatever the family: **indicative** |

How the families are fitted:
- **GPD and exponential:** fitted to the years above a threshold, with their lower bound at that threshold.
- **GEV:** fitted to all years, as a comparison only.
- **Nothing is picked automatically:** per tail (dry, wet), a person picks the family and threshold in the app (*Pastas → Familie kiezen*), using the return-period plot and the threshold plot. That plot shows the GPD shape and T=1000 per number of years kept; where they stop changing, the threshold is high enough.
- **Saving the pick:** picks are saved to `family_choice/<prefix>_family_choice.csv` and loaded by `load_family_choice`. A tail without a pick has no levels beyond T=100.

None of these numbers includes the uncertainty of the model itself, which was calibrated on a few years of heads and is taken as exact here. That uncertainty is probably larger than the difference between families. The bootstrap bands of `explore_shape_and_uncertainty` cover only the sampling of the pooled years.

## Tables in our workspace

All in `waterkeringbeheer_prd.jvanruitenbeek`, once per basis: `<prefix>` = `unvalid_pastas` or `valid_pastas`.

Every run appends with its own `run_id`, so old runs stay. Readers take the latest run per series and model type (`tarso_pipeline.results.latest_run_sql`).

| table / view | written by | one row per | holds |
|---|---|---|---|
| `<prefix>_series_manifest` | `build_manifest` | series | the work list: series spanning ≥ 365 days, span, count, duplicate timestamps (overwritten) |
| `<prefix>_fit_metrics` | `fit_pastas_models` | series × model type × run | success / error, nobs, tmin/tmax, EVP, R², holdout R², RMSE, AIC/BIC, t95, gain, `parameters` (JSON), forcing station and units, `.pas` path |
| `<prefix>_model_checks` | `fit_pastas_models` | check × fit | the Brakenhoff checklist: statistic, threshold, passed |
| `<prefix>_simulations` | `fit_pastas_models` | day × fit | observed and simulated daily head (the app and dashboard plot this) |
| `<prefix>_evt_selection` | `run_extremes` | model × tail | the automatic family per tail (for reference) |
| `<prefix>_extremes_summary` | `run_extremes` | model | pooled extremes, empirical T=100 per tail, GPD/GEV parameters |
| `<prefix>_return_levels` | `run_extremes` | model × tail × family × T | the return level |
| `<prefix>_model_selection` | `run_extremes` | series × model type | the checks, `passed`, `runaway`, `eligible`, `tier`, `selected`, `reason` |
| `<prefix>_shape_uncertainty`, `<prefix>_return_level_ci` | `explore_shape_and_uncertainty` (optional) | model × tail (× T) | bootstrap shape SE, 95 % bands |
| `<prefix>_family_choice`, `<prefix>_model_choice` | `load_family_choice` | model × tail / series | the picks made in the app (from git, overwritten) |
| view `<prefix>_return_levels_chosen` | `load_family_choice` | model × tail × T | the levels in the picked family |
| view `<prefix>_model_in_use` | `load_family_choice` | series | the model that applies, `handmatig` or `automatisch` |

On the volume, `/Volumes/waterkeringbeheer_prd/misc_files/misc_files/pastas_pipeline/<prefix>/run_yy_mm_dd/` holds:
- `pas_files/`;
- the result and diagnostics figures of the models in use;
- the pooled extremes and the return-period, threshold and QQ plots.

The monitoring job reads these tables into `monitoring_pastas_models` and `monitoring_pastas_series`.

## Running it

| notebook | purpose | writes |
|---|---|---|
| `exploration/explore_meting.py` | what is in the source; how many series qualify | nothing |
| `ingestion/build_manifest.py` | the work list | `<prefix>_series_manifest` |
| `transformation/fit_pastas_models.py` | clean, fit every model type, checks, holdout, `.pas` | `fit_metrics`, `model_checks`, `simulations`, `.pas` |
| `transformation/run_extremes.py` | IMPREX simulations, extremes, return levels, the pick, figures of the models in use | `evt_selection`, `extremes_summary`, `return_levels`, `model_selection` |
| `analysis/explore_shape_and_uncertainty.py` | optional: bootstrap SE and bands | `shape_uncertainty`, `return_level_ci` |
| `analysis/load_family_choice.py` | the app's picks (git) into Delta, plus the views | `family_choice`, `model_choice`, views |
| `analysis/export_results.py` | the latest results and plots into `results_export/` for the app (until it reads live) | files in the Git folder |
| `analysis/plot_models_in_use.py` | after a model pick in the app: the missing figures of the models now in use | PNGs |
| `analysis/inspect_results.py` | did it work, are the fits any good | nothing |

As one job:

```
03_validated_to_delta → build_manifest → fit_pastas_models → run_extremes → export_results
```

`explore_shape_and_uncertainty` is an optional branch off `run_extremes`.

Settings:
- **Same parameters:** give every task the same `data_basis` and `run_folder`. The run folder defaults to today's date, so a job that crosses midnight would otherwise split.
- **First run:** run the fit with `limit_series=1` first. The `WRITE VOLUME` grant is separate from the table grant, and the notebook probes it with a throwaway file before fitting.
- **Widgets:** `model_types` (comma-separated), `noise` (`none`/`ar1`/`arma`) and `holdout` (default 0.3) are widgets on the fit notebook.
- **Figures:** only the models in use get figures. The fit draws none (`save_plots=false`); `run_extremes` (`make_plots=in_use`) draws them after the pick, and `export_results` (`plot_models=in_use`) copies only those to git.

### Locally

```bash
pip install -e ".[pastas,dev]"
pytest -m "not slow"                 # no real fit
python scripts/run_local.py          # fit every validated series from the review files
python scripts/run_local.py --imprex-dir <IMPREX csv folder> --export
```

`run_local.py` reads the review stores in `data_validation/input_output/output_repository/`. Its options:
- `--imprex-dir` also runs the extremes;
- `--pooled-dir <pooled_extremes folder>` refits the pooled extremes of an earlier run;
- `--export` writes to `results_export/valid_pastas/`;
- `--model-types`, `--noise` and `--holdout` work as in the fit notebook, and `--workers` runs the extremes of that many models at once.

Run folders go to `local_runs/`, which is git-ignored.

## Refreshing the KNMI forcing

```bash
python scripts/check_knmi.py                       # incremental; station 249
python scripts/check_knmi.py --station 235 --backlog-start 1970-01-01
git add data/forcing && git commit -m "Refresh KNMI forcing" && git push
```

Then pull the Git folder and copy the CSV to the volume folder `…/pastas_pipeline/knmi_forcing/`. The fit notebook and the monitoring job read it there.

This route is a stopgap: refreshing needs a person at a laptop. Once outbound HTTPS to `www.daggegevens.knmi.nl` is allowed from the cluster, the forcing can be fetched on a schedule.

## Layout

```
src/tarso_pipeline/
  meting.py        SQL over the source: verdicts, manifest, duplicates
  clean.py         collapse duplicate timestamps + resample; counts recorded
  forcing.py, io.py, knmi.py, forcing_plot.py   forcing: load, coverage, KNMI download, chart
  fit.py           model types, build_model, fit_series (never raises), holdout R²
  fit_metrics.py   the fit_metrics row
  model_checks.py  the Brakenhoff (2022) checklist
  imprex.py, simulate.py, extremes.py, extremes_run.py   IMPREX members, simulation, block extremes
  evt.py, evt_report.py, evt_plots.py, uncertainty.py    return levels, plots, bootstrap
  selection.py     the stamp, the runaway check, the pick, the person's model choice
  results.py       latest run per series, the levels per pick (shared with the app)
  model_plots.py   figures of the models in use
  paths.py         every table name, volume path and the data_basis switch
notebooks/          see "Running it"
scripts/            run_local.py, check_knmi.py
results_export/     the app's copy of the latest results (in git)
family_choice/, model_choice/   the picks made in the app (in git)
data/forcing/       the committed KNMI CSVs
docs/               Databricks background: source schema, permissions, cs_objecten
tests/              pytest (no Spark; -m "not slow" skips the real fits)
```

## Conventions

- **Source names:** source columns are never renamed (`meetreeks_id`, `waarde`, `tijdstempel`, `validatie_status`). An alias is used only for a value the source does not have (`n_values`, `span_days`, `series_name`). `tests/test_meting.py` enforces this.
- **`series_name`:** `series_<first 24 hex of meetreeks_id>`. It is a label for files and folders, not a key; the full `meetreeks_id` is on every row.
- **`gold_prd` is read-only:** all output goes to `waterkeringbeheer_prd.jvanruitenbeek` and the `misc_files` volume (`paths.py`).
- **Old table names:** the tables used to be called `pastas_<kind>`. Rename them once to keep that history as the unvalidated basis:
  ```sql
  ALTER TABLE waterkeringbeheer_prd.jvanruitenbeek.pastas_fit_metrics  RENAME TO waterkeringbeheer_prd.jvanruitenbeek.unvalid_pastas_fit_metrics;
  ALTER TABLE waterkeringbeheer_prd.jvanruitenbeek.pastas_model_checks RENAME TO waterkeringbeheer_prd.jvanruitenbeek.unvalid_pastas_model_checks;
  -- likewise pastas_series_manifest, pastas_evt_selection, pastas_extremes_summary
  ```

Background on the source schema, permissions and the location registry: [`docs/`](docs/), in particular `DATABRICKS_HANDOFF.md` §2.0 and `DATABRICKS_PERMISSIONS.md`.
