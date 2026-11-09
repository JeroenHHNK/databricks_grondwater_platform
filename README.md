# Grondwater platform

One repository for the groundwater work on Databricks: a single Streamlit app
(Databricks App) with one section per platform, plus the Databricks notebooks of
all three projects.

| Folder | Project | What it holds | Was |
|---|---|---|---|
| [`boxplots/`](boxplots/README.md) | Boxplots peilbuizen | Cross-sections with soil build-up, wells and heads; the **Boxplots** section of the app, and its notebooks | `JeroenHHNK/databricks_boxplots-pipeline` (`development`) |
| [`data_validation/`](data_validation/README.md) | Groundwater data validation | Source copy → automated checks → manual review; the **Data validation** section of the app, and its Databricks job | `JeroenHHNK/databricks_data_validation` (`main`) |
| [`pastas/`](pastas/README.md) | Pastas/Tarso pipeline | Model fits and extremes, Databricks notebooks only (no app pages) | `JeroenHHNK/databricks_pastas_pipeline` (`main`) |

Each project came in with its full git history (`git log -- boxplots/` shows it) and keeps
its own `pyproject.toml`, `docs/`, notebooks, tests and data. Every project finds its
files by walking up to its **own** `pyproject.toml`, so its notebooks and scripts work
unchanged from inside its folder.

## The app

```bash
uv sync
uv run streamlit run app.py
```

`app.py` puts the projects side by side in the navigation pane (pages in `platform_app/nav.py`):

- **Boxplots**: Dwarsprofielen (the map, then a cross-section; `?profiel=BE_0072` links straight to one)
- **Stijghoogtes**: heads from the same source as Boxplots (daily means of the series Boxplots
  picks per well), in `platform_app/stijghoogtes.py`:
  - *Eén peilbuis*: one series with its daily min–max, filter levels and days the source marks invalid;
  - *Per dwarsprofiel*: all wells of a cross section, coloured by zone (BUKR, BIKR, BIT, …), filter by zone and depth;
  - *Zone vergelijken*: one zone (default BIKR) across cross sections and projects, with the median.
  The zone is read from the well code (`…_BIKR_GMW_PB2_…`); wells without a zone token show as `onbekend`.
  Every view is in the URL, and Boxplots links to *Per dwarsprofiel*.
- **Data validation**: Map, Review, Overview
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
- `DATABRICKS_WAREHOUSE_ID` (commented out): live heads for Boxplots and Stijghoogtes, see
  [`boxplots/DATA_CATALOG.md`](boxplots/DATA_CATALOG.md). Without it, both show the export.

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
uv run pytest tests                  # the central app's own code (platform_app/)
cd data_validation && uv run --project .. pytest tests
cd pastas && pip install -e ".[pastas,dev]" && pytest -m "not slow"
```

The Databricks background docs (`docs/`) were nearly identical in the three projects
and are kept per project for now.
