# tarso-pipeline

Minimal Pastas/Tarso training pipeline for Databricks: load head (groundwater level)
CSVs plus two meteorological forcing series, train one Pastas `TarsoModel` per head
series, persist the model and its fit statistics.

## Documentation

| File | Covers |
|---|---|
| [`docs/DATABRICKS_HANDOFF.md`](docs/DATABRICKS_HANDOFF.md) | The original spec, **plus §2.0: the Unity Catalog source schema** (`gold_prd.hl_grondwaterpeilmeting`, ten tables) |
| [`docs/PIPELINE_DATASETS.md`](docs/PIPELINE_DATASETS.md) | What the pipeline reads, holds and writes. Each section marked current or superseded |
| [`docs/DATABRICKS_PERMISSIONS.md`](docs/DATABRICKS_PERMISSIONS.md) | What we may read and write, the volume folder layout, and where the repo lives |
| [`docs/DATABRICKS_layer_cs_objecten.md`](docs/DATABRICKS_layer_cs_objecten.md) | The `gold_prd.hl_cs_objecten` object registry — `meetlocatie` would give each series coordinates and a water-level area |

`PIPELINE_DATASETS.md` was `DATA_CATALOG.md` until 2026-10-20; the old name read as if
it described Unity Catalog, which it never did.

## Layout

```
src/tarso_pipeline/
  meting.py        SQL over the meting table: verdicts, manifest, duplicates
  clean.py         collapse duplicate timestamps + resample; counts recorded
  fit.py           fit_series: load -> clean -> fit -> save -> metrics row
  fit_metrics.py   the pastas_fit_metrics row, read from the Pastas API
  model_checks.py  the pastas_model_checks rows: Brakenhoff (2022) via ps.check
  imprex.py        discover + load the bias-corrected IMPREX ensemble
  simulate.py      replace a Tarso model's stresses and simulate
  extremes.py      hydrological-year block extremes, pooled across members
  evt.py           empirical levels, GPD/exp above a threshold, GEV for comparison, threshold scan
  evt_report.py    the return-period table and the run roll-ups
  evt_plots.py     return_plots_fitted / qq_plots / confidence_plots
  extremes_run.py  run_model: simulate -> pool -> fit -> artefacts
  uncertainty.py   the bootstrap: GPD shape SE + 95% bands of GPD / exponential
  knmi.py          KNMI forcing download (precipitation + Makkink)
  forcing.py       load the forcing CSV (units, station) + warmup coverage
  forcing_plot.py  annual totals and the check chart
  io.py            load_forcing / read_head_csv
  naming.py        sanitize_name
  paths.py         repo_root, forcing_path, the Databricks write targets, per data basis
  results.py       latest run per series, the levels that apply per pick (shared with the app)
notebooks/
  exploration/explore_meting.py        what is in the source, how much is usable
  ingestion/build_manifest.py          JOB TASK 1 -> <prefix>_series_manifest
  transformation/fit_pastas_models.py  JOB TASK 2 -> <prefix>_fit_metrics + .pas
  transformation/run_extremes.py       stage 2: return levels -> <prefix>_return_levels
  analysis/explore_shape_and_uncertainty.py  optional: bootstrap SE + confidence bands
  analysis/inspect_results.py          read the output tables after a run
  analysis/export_results.py           the latest results into results_export/, for the app
  analysis/load_family_choice.py       family_choice/*.csv -> <prefix>_family_choice + view
scripts/run_local.py                   the validated chain locally: fit (+ extremes) -> export
results_export/    the app's copy of the results (export_results.py; in git)
family_choice/     the families picked in the app (in git)
scripts/
  check_knmi.py    refresh the committed forcing (runs locally, not on a cluster)
tests/             pytest suite (no Spark; -m "not slow" skips the real fits)
```

## Training

`notebooks/transformation/train_tarso.py` fits one `TarsoModel` per head series:
discover -> load forcing -> train (distributed) -> append to `tarso_runs`.

Only series spanning at least `min_days` (**default 365**) are fitted — a Tarso
fit needs a full seasonal cycle, and a few months of data gives a number that
looks like a result but is not one. Shorter series are **not dropped**: they get a
row with `status="skipped"` and the reason. Likewise a fit that throws becomes
`status="error"` with the message — one bad series never aborts the run.

```bash
pytest -m "not slow"   # fast: no Pastas fit
pytest                 # includes a real fit (needs the pastas extra)
```

## Local development

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"          # add ",pastas" once train.py lands
pytest
```

### Checking the KNMI ingestion without a cluster

The notebook itself needs Databricks (`dbutils`, `spark` and `display` are cluster
globals), but every decision it makes is plain pandas. `scripts/check_knmi.py` runs
**the same functions** against a local CSV instead of a Delta table, so the terminal
output matches what the job produces:

```bash
python scripts/check_knmi.py            # incremental; writes .local/ (gitignored)
python scripts/check_knmi.py --reset    # start the 2020 backlog over
python scripts/check_knmi.py --station 235
```

It prints the fetch window, the coverage check and the annual totals, and saves the
chart as a PNG. Run it twice to see the incremental path: the second run fetches only
the lookback overlap and the row count stays put.

To run the Spark and Delta parts locally as well, use
[Databricks Connect](https://docs.databricks.com/dev-tools/databricks-connect/)
(`pip install databricks-connect`), which executes against a real cluster from the IDE.

## Forcing data ships with the repo

The cluster has no outbound internet (`Failed to resolve 'www.daggegevens.knmi.nl'`
— a DNS/egress block, not an authorisation problem; the API needs no credentials).
So the forcing is fetched locally and **committed**, and a Git folder pull is the
whole deployment:

```bash
python scripts/check_knmi.py      # fetch + refresh data/forcing/knmi_forcing_249.csv
git add data/forcing && git commit -m "Refresh KNMI forcing" && git push
#   then: pull the Git folder in Databricks
```

The file is ~58 KB for 2020-to-date and only grows by a line a day, so it is
cheap to carry. Committing it also pins the data version to the commit that used
it, which is worth something on its own.

Downstream notebooks read it through the normal loader — no KNMI-specific code,
and no hardcoded `/Workspace/...` path:

```python
from tarso_pipeline.io import load_forcing
from tarso_pipeline.paths import forcing_path

path = forcing_path(249)                            # resolves inside the Git folder
precipitation = load_forcing(path, "precipitation_mm")   # pd.Series, mm/day
evaporation   = load_forcing(path, "makkink_mm")         # pd.Series, mm/day
```

The ingestion notebook has a `source` widget for the three cases:

| `source` | Reads | When |
|---|---|---|
| `repo` *(default)* | `data/forcing/knmi_forcing_<station>.csv` | no cluster egress — the current situation |
| `api` | KNMI directly | once outbound HTTPS to `www.daggegevens.knmi.nl:443` is allowed |
| `file` | an explicit `source_path` | a Volume or `/Workspace/Users/...` copy |

All three produce the same frame, so the `MERGE`, the coverage check and the chart
are identical code paths.

**The committed-file route is a stopgap**: refreshing needs a person at a laptop,
so the pipeline cannot run unattended. The real fix is cluster egress, after which
`source=api` runs on a schedule and `data/forcing/` can be dropped.

## Databricks

Add this repo as a **Git folder** (Workspace > Create > Git folder). In a notebook or
job task:

```python
%pip install -e /Workspace/Repos/<user>/<repo>      # or build a wheel and attach it
```

Source files are never renamed: model names are derived from file stems with
`sanitize_name`. Head files are read from Unity Catalog Volume paths
(`/Volumes/<catalog>/<schema>/<volume>/...`).


## The Databricks pipeline

Each notebook has one job. Only two are job tasks; the other two are run by hand.

| Notebook | Purpose | Job task? | Writes |
|---|---|---|---|
| `exploration/explore_meting.py` | what is in the source, how many series are fit to model | no | nothing |
| `ingestion/build_manifest.py` | select every series spanning >= `min_days` (365); quantify duplicate timestamps | **task 1** | `<prefix>_series_manifest` |
| `transformation/fit_pastas_models.py` | clean, fit, save `.pas`, record metrics, run the model checks | **task 2** | `<prefix>_fit_metrics`, `<prefix>_model_checks`, `<prefix>_simulations`, `.pas` files |
| `transformation/run_extremes.py` | synthetic extremes per model; empirical levels, GPD/exp per threshold, GEV for comparison; return-period and threshold plots; then which model each series uses | by hand | `<prefix>_evt_selection`, `<prefix>_extremes_summary`, `<prefix>_return_levels`, `<prefix>_model_selection` |
| `analysis/explore_shape_and_uncertainty.py` | optional, after `run_extremes`: bootstrap shape SE vs the Hessian one (`families_agree`), 95% band per return level, `confidence_plots/` | optional task | `<prefix>_shape_uncertainty`, `<prefix>_return_level_ci` |
| `analysis/plot_models_in_use.py` | after a model pick in the app: the missing figures of the models in use, optionally removing the figures of the others | no | PNGs in the run folder |
| `analysis/inspect_results.py` | did it work, are the fits any good, the model types side by side | no | nothing |
| `analysis/export_results.py` | the latest run per series, into `results_export/` for the app (until it reads live), plus the newest extremes plots per model | no | CSVs / parquet / `plots/*.png` in the Git folder |
| `analysis/load_family_choice.py` | the families and models picked in the app (`family_choice/`, `model_choice/`) into Delta | no | `<prefix>_family_choice`, `<prefix>_model_choice`, views `<prefix>_return_levels_chosen`, `<prefix>_model_in_use` |

Task 2 depends on task 1: it reads the manifest table task 1 writes.

As one job: `03_validated_to_delta -> build_manifest -> fit_pastas_models -> run_extremes ->
export_results`, with `explore_shape_and_uncertainty` as an optional branch off
`run_extremes`. Give every task the same `data_basis` and `run_folder`: the run folder
defaults to today's date, so a job that crosses midnight would otherwise split.

### Model types, and which one a series uses

Every series is fitted with five model types (`tarso_pipeline.fit.MODEL_SPECS`): **Tarso**
and **linear recharge** (`R = P - f E`) with an Exponential, Gamma, DoubleExponential or
FourParam response (`linear_exp`, `linear_gamma`, `linear_dexp`, `linear_fourparam`). A grid
test on the validated series (Tarso, Linear, Berendrecht, Peterson and FlexModel x four
responses) left these: Berendrecht hit its parameter bounds on every series and ran away in the
extremes, FlexModel fitted worse, Peterson won AIC but predicted unseen data worse
(`peterson_gamma` can still be fitted). Each fit also gets `r2_holdout`: the R2 on the last
30 % of the observations of a fit on the first 70 %.

`run_extremes` runs the ensemble for every model, then picks one per series
(`tarso_pipeline.selection`, `<prefix>_model_selection`):

1. **the stamp `passed`**: R2 >= 0.7, response memory, gain and parameter bounds (Brakenhoff
   2022). The runs test is reported (`pass_runs`) but does not count: without a noise model
   daily residuals are always autocorrelated, and an AR(1) or ARMA(1,1) noise model did not
   change that on the validated series (`noise` widget, off by default);
2. **no runaway**: a T=100 level more than 1.5 m beyond the observed range rules a model out;
3. among the passed models (else all), not more than 0.05 below Tarso on `r2_holdout`, the
   **lowest AIC**; a model with fewer parameters within 2 AIC wins.

**Figures only for the model in use.** With five fits per series, drawing everything would put five `ml.plots.results()`, diagnostics, return-period, threshold and QQ figures per series in the run folder. So `fit_pastas_models` draws none (`save_plots=false`) and `run_extremes` (`make_plots=in_use`) draws them after the selection, for the model each series uses, from the `.pas` and the pooled extremes (`tarso_pipeline.model_plots`). `export_results` (`plot_models=in_use`) copies only those to git and removes the others. `make_plots=all` / `plot_models=all` restore the old behaviour.

A person can overrule the pick per series in the app (*Pastas → Modellen →
Modelvergelijking en keuze*, saved to `model_choice/<prefix>_model_choice.csv`). The Boxplots
draw the return levels of the model in use, in the family picked for that model.

The fitted parameters of every model type share one column: `fit_metrics.parameters` is a JSON
object `{name: value}` (`parameters_stderr` and `parameters_bounds` likewise), so a new model
type needs no new columns; `n_param`, `t95_days` and `gain` are the comparable summaries.

### Return levels: what is used where

| T | level |
|---|---|
| <= 100 | **empirical**, from the ~704 pooled synthetic years (~7 exceedances at T=100): no distribution needed |
| 250-500 | the **picked family** above the **picked threshold**: `gpd` (the tail flattens off) or `exp` (it does not) |
| 1000 | the same, but extrapolation whatever the family: **indicative** |

The GPD and exponential are fitted to the years above a threshold, with their lower bound at
that threshold (the coherent model for values above a threshold); the GEV only to all years,
as a comparison - fitted to the top part it overstates T=1000 by decimetres to metres. Nothing
is chosen automatically: per tail a person picks the family and the threshold in the app
(*Pastas → Familie kiezen*), from the return-period plot and the threshold plot (GPD shape and
T=1000 per number of years kept; where they stop changing, the threshold is high enough).

None of these numbers contains the uncertainty of the Tarso model itself, calibrated on a few
years of heads and taken as exact here; that is probably larger than the difference between
families. The bootstrap bands of `explore_shape_and_uncertainty` cover only the sampling of
the pooled years.

### Running it locally

`python scripts/run_local.py` fits every validated series from the review files in
`data_validation/input_output/output_repository/` (needs the `pastas` extra). With
`--imprex-dir <folder with the IMPREX member CSVs>` it also runs the extremes, or with
`--pooled-dir <a pooled_extremes folder>` it refits pooled extremes of an earlier run;
`--export` writes the results to `results_export/valid_pastas/` for the app. Run folders go
to `local_runs/` (git-ignored). `--model-types`, `--noise` and `--holdout` as the fit notebook;
`--workers` runs the extremes of that many models at once.

### Validated or unvalidated data (`data_basis`)

Every notebook has a `data_basis` widget, and everything it reads and writes follows from it
(`tarso_pipeline.paths.output_table` / `source_table` / `basis_volume`):

| `data_basis` | reads | tables | artefacts |
|---|---|---|---|
| `unvalidated` (default) | `gold_prd.hl_grondwaterpeilmeting.meting`, every valued row | `unvalid_pastas_<kind>` | `<volume>/unvalid_pastas/run_yy_mm_dd/` |
| `validated` | `waterkeringbeheer_prd.jvanruitenbeek.validated_meting`: only the hours approved in the data validation review, with corrections applied (written by `data_validation/.../03_validated_to_delta.py`, same columns as `meting`) | `valid_pastas_<kind>` | `<volume>/valid_pastas/run_yy_mm_dd/` |

The two sets of tables are identical in shape, so the fits compare row for row (the app's
*Pastas → Gevalideerd vs ongevalideerd*). Run the whole chain once per basis. The validated
source is hourly means rather than the raw ~55 rows a day; the cleaning resamples to days either
way.

The tables used to be called `pastas_<kind>` and the run folders sat directly under the volume.
To keep those runs as the unvalidated history, rename them once:

```sql
ALTER TABLE waterkeringbeheer_prd.jvanruitenbeek.pastas_fit_metrics  RENAME TO waterkeringbeheer_prd.jvanruitenbeek.unvalid_pastas_fit_metrics;
ALTER TABLE waterkeringbeheer_prd.jvanruitenbeek.pastas_model_checks RENAME TO waterkeringbeheer_prd.jvanruitenbeek.unvalid_pastas_model_checks;
-- likewise pastas_series_manifest, pastas_evt_selection, pastas_extremes_summary
```

### Results in the app, and the family pick

The fit notebook also writes `<prefix>_simulations` (daily observed + simulated head per fit) and
the extremes notebook `<prefix>_return_levels` (one row per tail x family x return period). The
app plots those, live through the SQL warehouse or from the export in `results_export/`; see the
root README, "Pastas results in the app". The family and threshold per model and tail are picked
in the app (*Pastas → Familie kiezen*, locally), saved to `family_choice/<prefix>_family_choice.csv`,
and taken to Delta by `analysis/load_family_choice.py`. A tail without a pick has no levels.

Permission facts live in `src/tarso_pipeline/paths.py`. `gold_prd` is **read-only**;
all output goes to `waterkeringbeheer_prd.jvanruitenbeek`, and `.pas` files to the
`misc_files` volume.

Run task 2 with `limit_series=1` first. The `WRITE VOLUME` grant on `misc_files` is
separate from the table write, and the notebook probes it with a throwaway file
before fitting anything.

The stress series are **not** fetched at run time — they ship in `data/forcing/`
because the cluster has no outbound internet. Refresh them locally:

```bash
# full history for a station (one API call); 249 starts 1999, 235 starts 1970
python scripts/check_knmi.py --station 249 --backlog-start 1970-01-01
git add data/forcing && git commit -m "Refresh KNMI forcing" && git push
```

Then upload the CSV to the volume the fit notebook reads:

```
/Volumes/waterkeringbeheer_prd/misc_files/misc_files/pastas_pipeline/knmi_forcing/
```

| station | range | full 3650-day warmup needs head starting |
|---|---|---|
| **249 Berkhout** (in use) | 1999-03-23 onwards | 2009 or later |
| 235 De Kooy | 1970-01-01 onwards | 1980 or later |

The head series run from **2022**, so 249 gives ~8300 days before them and the full
3650-day warmup fits. 235 is only needed if series older than 2009 appear; regenerate
it with `python scripts/check_knmi.py --station 235 --backlog-start 1970-01-01`.

At the other end, KNMI publishes 1-2 days behind, so a series running to today always
overshoots the forcing. `tmax` is trimmed back to the last forcing day; the drop is
reported and recorded (`calibration_trimmed`, `days_trimmed_from_end`). No station
avoids this - refreshing the forcing recovers those days.

## Naming: source columns are never renamed

A column that exists in Unity Catalog keeps its Unity Catalog name everywhere —
in the SQL, the output tables, the CSV artefacts and the Python signatures:

| Source column | Meaning | Pipeline name |
|---|---|---|
| `meetreeks_id` | series key (64-char hash) — **the primary key** | `meetreeks_id` |
| `waarde` | measured head | `waarde` |
| `tijdstempel` | observation timestamp | `tijdstempel` |
| `validatie_status` | reviewer verdict | `validatie_status` |
| `id` | per-measurement row key (unused) | `id` |

An alias is only ever used for a value the source does **not** have — `n_values`,
`span_days`, `verdict`, `series_name`. So any name the pipeline invents is visibly
a derived quantity, never a second name for something that already had one, and
the key can be traced from `gold_prd` to the return-period CSV by reading it.

`tests/test_meting.py` enforces this: it parses every generated statement and
fails if a bare source column is aliased to anything but itself.

`series_name` (`series_<first 24 hex of meetreeks_id>`) is the one invented
identifier, because filenames and folder names need something shorter than 64
characters. It is a label, not a key — the full `meetreeks_id` is on every output
row, so a join always recovers it.

**`validatie_status` is ignored** on the raw source: with `data_basis=unvalidated` every row
with a `waarde` is used, whatever its status. To train on reviewed data, use
`data_basis=validated` (above).

**Forcing units.** The fit notebook converts mm to **metres per day** by default
(`forcing_units=m`), matching the head in metres NAP. The original repo passed
millimetres straight through; the choice only rescales the gain parameters, but it
makes the two conventions incomparable, so it is recorded on every metrics row.
