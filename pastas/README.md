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
  evt.py           GEV/exp/GPD fits, return levels, family selection (Hessian shape SE)
  evt_report.py    the return-period table and the run roll-ups
  evt_plots.py     return_plots_fitted / qq_plots / confidence_plots
  extremes_run.py  run_model: simulate -> pool -> fit -> artefacts
  uncertainty.py   the GEV bootstrap: shape SE check + 95% return-level band
  knmi.py          KNMI forcing download (precipitation + Makkink)
  forcing.py       load the forcing CSV (units, station) + warmup coverage
  forcing_plot.py  annual totals and the check chart
  io.py            load_forcing / read_head_csv
  naming.py        sanitize_name
  paths.py         repo_root, forcing_path, the Databricks write targets, per data basis
  results.py       latest run per series, the family that applies (shared with the app)
notebooks/
  exploration/explore_meting.py        what is in the source, how much is usable
  ingestion/build_manifest.py          JOB TASK 1 -> <prefix>_series_manifest
  transformation/fit_pastas_models.py  JOB TASK 2 -> <prefix>_fit_metrics + .pas
  transformation/run_extremes.py       stage 2: return levels -> <prefix>_return_levels
  analysis/explore_shape_and_uncertainty.py  optional: bootstrap SE + confidence bands
  analysis/inspect_results.py          read the output tables after a run
  analysis/export_results.py           the latest results into results_export/, for the app
  analysis/load_family_choice.py       family_choice/*.csv -> <prefix>_family_choice + view
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
| `transformation/run_extremes.py` | synthetic extremes, EVT fits, family choice (Hessian shape SE), return levels | by hand | `<prefix>_evt_selection`, `<prefix>_extremes_summary`, `<prefix>_return_levels` |
| `analysis/explore_shape_and_uncertainty.py` | optional, after `run_extremes`: bootstrap shape SE vs the Hessian one (`families_agree`), 95% band per return level, `confidence_plots/` | optional task | `<prefix>_shape_uncertainty`, `<prefix>_return_level_ci` |
| `analysis/inspect_results.py` | did it work, are the fits any good, Tarso vs baseline | no | nothing |
| `analysis/export_results.py` | the latest run per series, into `results_export/` for the app (until it reads live), plus the newest extremes plots per model | no | CSVs / parquet / `plots/*.png` in the Git folder |
| `analysis/load_family_choice.py` | the families picked in the app (`family_choice/`) into Delta | no | `<prefix>_family_choice`, view `<prefix>_return_levels_chosen` |

Task 2 depends on task 1: it reads the manifest table task 1 writes.

As one job: `03_validated_to_delta -> build_manifest -> fit_pastas_models -> run_extremes ->
export_results`, with `explore_shape_and_uncertainty` as an optional branch off
`run_extremes`. Give every task the same `data_basis` and `run_folder`: the run folder
defaults to today's date, so a job that crosses midnight would otherwise split.

`run_extremes` picks a tail's family with the shape SE from the curvature of the GEV
likelihood (milliseconds). The bootstrap it used to run (200 refits per tail, plus 500 per
tail for the confidence band) now lives in `explore_shape_and_uncertainty`, which reads
the pooled extremes and needs no new simulation. Set `shape_se_method=bootstrap` on
`run_extremes` to choose families the old way.

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
root README, "Pastas results in the app". The statistical family per model and tail is picked in
the app (*Pastas → Modellen*, locally), saved to `family_choice/<prefix>_family_choice.csv`, and
taken to Delta by `analysis/load_family_choice.py`. Without a pick, the automatic choice of
`select_family` applies.

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
