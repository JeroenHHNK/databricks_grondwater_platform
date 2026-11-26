# Grondwater platform

One repository for the groundwater work on Databricks: a single Streamlit app
(Databricks App) with one section per platform, plus the Databricks notebooks of
all three projects.

| Folder | Project | What it holds | Was |
|---|---|---|---|
| [`boxplots/`](boxplots/README.md) | Boxplots peilbuizen | Cross-sections with soil build-up, wells and heads; the **Boxplots** section of the app, and its notebooks | `JeroenHHNK/databricks_boxplots-pipeline` (`development`) |
| [`data_validation/`](data_validation/README.md) | Groundwater data validation | Source copy → automated checks → manual review; the **Data validation** section of the app, and its Databricks job | `JeroenHHNK/databricks_data_validation` (`main`) |
| [`pastas/`](pastas/README.md) | Pastas/Tarso pipeline | Model fits and extremes on validated or unvalidated data, Databricks notebooks; the **Pastas** section of the app reads their results | `JeroenHHNK/databricks_pastas_pipeline` (`main`) |
| [`monitoring/`](monitoring/README.md) | Monitoring | The daily job that writes the `monitoring_*` tables, and the generator of the `Grondwater_Monitoring` dashboard | new |
| [`databricks_dashboard/`](databricks_dashboard/README.md) | Dashboards | The Databricks AI/BI dashboards (`.lvdash.json`): what each one monitors and with which data | new |

Each project came in with its full git history (`git log -- boxplots/` shows it) and keeps
its own `pyproject.toml`, notebooks, tests and data. Every project finds its
files by walking up to its **own** `pyproject.toml`, so its notebooks and scripts work
unchanged from inside its folder.

## How the repository works

### From measurement to return level

```
gold_prd (read-only source: heads, series, locations)      KNMI / RWS / IMPREX (forcing, in git)
   │                                                                │
   ▼  data_validation, on Databricks (notebooks 00-02)              │
copy + one CSV per series ──git──► laptop: automatic flags, then a person
                                    reviews in the app (approve / reject / correct)
                                           │  review stores ──git──►
                                           ▼
             Databricks 03_validated_to_delta ──► validated_meting (Delta)
                                           │
                                           ▼
pastas, on Databricks (or scripts/run_local.py on a laptop)  ◄───────┘
   fit every model type per series ─► checks ─► IMPREX extremes ─► one model picked per series
   ──► valid_pastas_* / unvalid_pastas_* tables, .pas files on the volume
                                           │  export_results ──git──► (or live SQL)
                                           ▼
the app (Databricks App, Streamlit): Boxplots cross sections with return levels, Peilbuizen,
Pastas (models, picks), Data validation          monitoring: daily job ─► Grondwater_Monitoring dashboard
```

1. **Data validation** ([`data_validation/`](data_validation/README.md)): copies the heads from the source, flags suspect hours automatically, and lets a person approve, reject or correct them. The approved hours become `validated_meting`.
2. **Pastas** ([`pastas/`](pastas/README.md)): fits time-series models that explain each head from rain and evaporation (plus lake level, air pressure and a soil-moisture bucket), checks them, and picks one per series. It drives the picked models with 16 × 50 years of synthetic climate (IMPREX) to estimate how high or low the head gets once in T years.
3. **Boxplots** ([`boxplots/`](boxplots/README.md)): the dike cross sections, with the wells, the heads and those return levels.
4. **Monitoring** ([`monitoring/`](monitoring/README.md), [`databricks_dashboard/`](databricks_dashboard/README.md)): a daily job and a dashboard showing what is measured, reviewed and modelled, and which models are up for review.

### Where things run

| where | what | why there |
|---|---|---|
| **Databricks** (jobs, notebooks) | everything that reads `gold_prd` or writes Delta tables: the source copy, `validated_meting`, the Pastas runs, monitoring | the data and the compute are there |
| **Laptop** | the review (saving decisions), picks in the app, `pastas/scripts/run_local.py`, research, tests | a Databricks App cannot save files or push to git |
| **Databricks App** | the central app, read-only (`VALIDATION_READ_ONLY=1`) | for everyone to look at |

`gold_prd` is never written to. Everything we write goes to `waterkeringbeheer_prd.jvanruitenbeek` and the `misc_files` volume.

### Git carries data between the laptop and Databricks

The laptop and Databricks share no file system, so some data travels through this repository. **Pull before a run, push after saving.**

| what | from → to | where in the repo |
|---|---|---|
| one CSV per series, for the review | Databricks → laptop | `data_validation/input_output/output_databricks/` |
| review decisions (approve / reject / correction / note) | laptop → Databricks | `data_validation/input_output/output_repository/` |
| family and model picks made in the app | laptop → Databricks | `pastas/family_choice/`, `pastas/model_choice/` |
| model settings and per-series exceptions | laptop → Databricks | `pastas/config/` |
| KNMI, radar, lake and pressure forcing, IMPREX members | laptop → Databricks | `pastas/data/forcing/` |
| the latest Pastas results, for the app until it reads live | Databricks (or a local run) → app | `pastas/results_export/` |

### Changing something

| to change | edit | then |
|---|---|---|
| a review decision | *Data validation → Review* in the app, locally | push, run `03_validated_to_delta` |
| which model or family a series uses | *Pastas* in the app, locally | push, run `load_family_choice` |
| a selection threshold, the candidate model types, the bucket | [`pastas/config/model_settings.toml`](pastas/config/README.md) | push; on Databricks rerun `run_extremes` (thresholds) or the full chain (model types, bucket) |
| an exception for one series (no lake stress, extremes not trusted) | [`pastas/config/series_overrides.csv`](pastas/config/README.md) | push, rerun the fit |
| a table name or volume path | `pastas/src/tarso_pipeline/paths.py` | – |
| a page of the app | `platform_app/` (the navigation is `platform_app/nav.py`) | redeploy the app |

Research that might change the pipeline is done on a `research/…` branch, in `pastas/research/`, and logged in [`docs/experiments/`](docs/experiments/README.md): the question, the decision rule fixed before the results, the results and the mistakes. [`docs/experiments/NEXT_STEPS.md`](docs/experiments/NEXT_STEPS.md) is where to continue.

### Branches

```
feature/… · fix/… · research/…  ──PR──►  development  ──►  beta-0.1  ──►  main
```

- Work happens on a branch from `development` and goes back by pull request. Nothing goes straight to `main`.
- `beta-0.1` is the release branch toward `v0.1.0-beta`.
- GitHub Actions runs every test suite on each push and pull request.

### Top-level layout

| path | what |
|---|---|
| `app.py`, `app.yaml` | the central app and its Databricks App settings |
| `platform_app/` | the app's pages that combine projects (Peilbuizen, Pastas, Refresh data, access checks) |
| `pyproject.toml`, `uv.lock` | the app's dependencies (what the Databricks App installs) |
| `boxplots/`, `data_validation/`, `pastas/`, `monitoring/`, `databricks_dashboard/` | the projects, each with its own README |
| `docs/` | the shared Databricks docs (source schema, permissions, location registry) and `experiments/` |
| `scripts/run_tests.py`, `tests/`, `.github/workflows/tests.yml` | every test suite, the app's tests, CI |

## The app

```bash
uv sync
uv run streamlit run app.py
```

`app.py` puts the projects side by side in the navigation pane (pages in `platform_app/nav.py`):

- **Boxplots**: Dwarsprofielen (the map, then a cross-section; `?profiel=BE_0072` links straight to one).
  The cross section also shows the **return levels** of the wells' Pastas models (triangle up = wet,
  down = dry; T=10 and 100 by default), in the statistical family picked per model, trained on
  validated or unvalidated data (`platform_app/herhalingstijden.py`).
- **Peilbuizen** (was *Stijghoogtes*):
  - *Kaart*: every series Databricks has (~670, from the data validation export) on a map, one marker
    per well; click one, or search by code, location name or vendor, to see its daily head with the
    daily min–max, filter levels and, once reviewed, the validated line (approved hours with the
    correction applied). Series not linked to `hl_cs_objecten` have no position and are found by
    search. Heads are live per series when the warehouse is set, otherwise the hourly export
    (`platform_app/peilbuizen.py`; `?reeks=<filename>` links straight to one);
  - *Per dwarsprofiel*: all wells of a cross section, coloured by zone (BUKR, BIKR, BIT, …), filter by zone and depth;
  - *Zone vergelijken*: one zone (default BIKR) across cross sections and projects, with the median.
  The last two use the same source as Boxplots (daily means of the series Boxplots picks per well,
  `platform_app/stijghoogtes.py`). The zone is read from the well code (`…_BIKR_GMW_PB2_…`); wells
  without a zone token show as `onbekend`. Every view is in the URL, and Boxplots links to *Per dwarsprofiel*.
- **Pastas** (`platform_app/pastas_pagina.py`):
  - *Modellen*: every model of one data basis (unvalidated / validated) with EVP, R², RMSE, model checks
    and the family per tail; pick one for observed vs. simulated head (optionally with the other
    basis's model), the return levels of each family, and the **family pick** per tail (dry / wet);
  - *Gevalideerd vs ongevalideerd*: the same series on both bases, fit statistics and T=10/100 levels
    side by side with their differences.
- **Data validation**: Map, Review (now with *Correction*: one offset for every hour in a period),
  Overview, and *Data sources* (per series: blue = from the API, green = from `hl_cs_objecten`;
  an empty green cell is what still has to be linked)
- **Data → Refresh data**: the one place to reload data in the app, the steps to refresh each
  project's source data, and a check of live access (below). The project pages have no refresh
  buttons of their own in the central app.

The root `pyproject.toml` and `uv.lock` are the app's dependencies; Databricks Apps
installs from them. When app code in a project needs a new package, add it there as well
and run `uv lock`.

**Deploy:** Compute → Apps → the app → Deploy, with this repo's Git folder root (the
folder with `app.yaml`) as source. `app.yaml` holds the environment:

- `VALIDATION_READ_ONLY=1`: the Review page works but cannot save. A Databricks App's
  files are not kept and cannot be pushed to git, so review decisions are made locally
  (`uv run streamlit run app.py`, where saving is on) and pushed as before. Without the
  variable, saving is off inside a Databricks App and on everywhere else.
- `DATABRICKS_WAREHOUSE_ID` (commented out): live heads for Boxplots and Peilbuizen, see
  [`boxplots/DATA_CATALOG.md`](boxplots/DATA_CATALOG.md), and live Pastas results. Without it,
  all of them show the export in git.

### Live data and permissions

*Refresh data → Live data access* checks, as the app's service principal (or, with Databricks
Apps user authorization switched on, as you), whether it can reach the SQL warehouse, read the
three `gold_prd` tables the heads come from, see the validation schema and read
`validated_meting`; optionally it creates and drops a scratch table to test writing. Every
failure comes with the `GRANT` statements to fix it, filled in with the identity's name. It
works before live is switched on: type the warehouse ID on the page. Locally it runs as your own
`databricks auth login` profile. The checks are in `platform_app/toegang.py`.

The schema for validation results is `VALIDATION_SCHEMA` (default
`waterkeringbeheer_prd.jvanruitenbeek`, where `03_validated_to_delta` writes today).

### Pastas results in the app

The app can show a Databricks run as soon as it is written; it needs no admin rights on the
workspace, only three things an owner can grant (the check above lists what is missing, with
the SQL):

1. the SQL warehouse as an app resource (`sql-warehouse`, *Can use*) and `DATABRICKS_WAREHOUSE_ID`
   uncommented in `app.yaml`, the same switch as live heads;
2. `USE CATALOG` on `waterkeringbeheer_prd` and `USE SCHEMA` on `jvanruitenbeek` for the app's
   service principal (catalog / schema owner);
3. `SELECT` on the `unvalid_pastas_*` and `valid_pastas_*` tables the app reads (`fit_metrics`,
   `model_checks`, `simulations`, `evt_selection`, `return_levels`); the owner of those tables,
   i.e. whoever ran the notebooks, can grant this.

The plots are drawn from `<prefix>_simulations` (daily observed + simulated head, written by the
fit notebook), not from the PNGs on the volume, so no volume access is needed. Until live is on,
`pastas/notebooks/analysis/export_results.py` writes the latest run to `pastas/results_export/`
for git, the same route as the Boxplots export.

The **family pick** is saved like the review decisions: locally in
`pastas/family_choice/<prefix>_family_choice.csv` (the deployed app is read-only), pushed, then
`pastas/notebooks/analysis/load_family_choice.py` writes `<prefix>_family_choice` and the view
`<prefix>_return_levels_chosen` (the latest levels in the picked family, else the automatic one).

### Validated data, end to end

1. Review in *Data validation → Review* (locally): approve / reject hours, and correct a period
   where a sensor hung at another depth (*Correction*: start, end, offset in m). Push
   `data_validation/input_output/output_repository/`.
2. In Databricks, `03_validated_to_delta` writes `validated_meting`: the approved hours with the
   same columns as the source `meting` (`meetreeks_id`, `tijdstempel`, `tijdstempel_utc`,
   `waarde` = raw + correction, `validatie_status` = 2), plus the raw value and the correction.
3. Run the Pastas notebooks with `data_basis=validated`: they read `validated_meting` and write
   `valid_pastas_*`, the same tables as `unvalid_pastas_*` from `data_basis=unvalidated`.

The standalone apps still run on their own: `boxplots/app.py` and
`data_validation/streamlit_app.py`.

## On Databricks

Add this repo once as a Git folder. Point notebooks and jobs at the project subfolder:

- Boxplots notebooks: `boxplots/notebooks/`
- Data validation job: `data_validation/databricks_code/jobs/workflow_config.json` (already
  points at this repo and the `data_validation/` paths); for `scripts/run_sample_job.py`,
  `--repo-path` is `<git folder>/data_validation`.
- Pastas notebooks: `pastas/notebooks/`; the `source_root` widget, when set, is
  `<git folder>/pastas`.

## Tests

```bash
uv sync
uv run python scripts/run_tests.py          # every suite; skips the real Pastas fits
uv run python scripts/run_tests.py --slow   # also the real fits
```

It runs the suites one after the other, each from its own folder with its own pytest settings:
`tests/` (the central app), `data_validation/`, `monitoring/`, `boxplots/` and `pastas/`. The Pastas
suite gets `pastas==1.12.*` on the fly (`uv run --with`): it is not in `uv.lock`, because the
Databricks App installs from that lock and does not need it. GitHub Actions runs the same on every
push and pull request (`.github/workflows/tests.yml`), on Python 3.11 like the App.

The Databricks background docs are in [`docs/`](docs/), shared by all projects: the source
schema (`DATABRICKS_HANDOFF.md` §2.0), the grants and volume layout (`DATABRICKS_PERMISSIONS.md`)
and the location registry (`DATABRICKS_layer_cs_objecten.md`). `pastas/docs/PIPELINE_DATASETS.md`
describes what the Pastas pipeline reads and writes.
