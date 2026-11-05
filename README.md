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

`app.py` puts the projects side by side in the navigation pane:

- **Boxplots**: Dwarsprofielen (the map, then a cross-section; `?profiel=BE_0072` links straight to one)
- **Data validation**: Map, Review, Overview

The root `pyproject.toml` and `uv.lock` are the app's dependencies; Databricks Apps
installs from them. When app code in a project needs a new package, add it there as well
and run `uv lock`.

**Deploy:** Compute → Apps → the app → Deploy, with this repo's Git folder root (the
folder with `app.yaml`) as source. `app.yaml` holds the environment:

- `VALIDATION_READ_ONLY=1`: the Review page works but cannot save. A Databricks App's
  files are not kept and cannot be pushed to git, so review decisions are made locally
  (`uv run streamlit run app.py`, where saving is on) and pushed as before. Without the
  variable, saving is off inside a Databricks App and on everywhere else.
- `DATABRICKS_WAREHOUSE_ID` (commented out): live heads for Boxplots, see
  [`boxplots/DATA_CATALOG.md`](boxplots/DATA_CATALOG.md). Without it, Boxplots shows its export.

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
cd data_validation && uv run --project .. pytest tests
cd pastas && pip install -e ".[pastas,dev]" && pytest -m "not slow"
```

The Databricks background docs (`docs/`) were nearly identical in the three projects
and are kept per project for now.
