# tarso-pipeline

Minimal Pastas/Tarso training pipeline for Databricks: load head (groundwater level)
CSVs plus two meteorological forcing series, train one Pastas `TarsoModel` per head
series, persist the model and its fit statistics.

## Layout

```
src/tarso_pipeline/
  naming.py        sanitize_name: file stem -> valid Pastas model name
  io.py            read_head_csv (raw/validated detection), load_forcing
  preprocess.py    prepare_head: trim, resample, dropna, min_obs skip
  knmi.py          KNMI daily forcing download (precipitation + Makkink)
  forcing_plot.py  annual totals and the check chart
  paths.py         repo_root / forcing_path
  discover.py      list head CSVs -> (survey, file_path, series_name)
  train.py         train_tarso (the fit) + train_one (never raises)
  meting.py        SQL over gold_prd...meting: verdicts, manifest, duplicates
  clean.py         dedup duplicate timestamps + resample, counts recorded
  fit.py           fit_series: load -> clean -> fit -> save -> metrics row
  fit_metrics.py   the pastas_fit_metrics row, read from the API
notebooks/
  ingestion/knmi_forcing.py          KNMI -> Delta, incremental
  ingestion/find_trainable_series.py how many series are fit to model
  ingestion/build_manifest.py        task 1: the 20 longest + duplicate check
  transformation/fit_pastas_models.py task 2: fit -> .pas + pastas_fit_metrics
  transformation/train_tarso.py      CSV-on-a-Volume variant (not the meting path)
scripts/
  check_knmi.py    run the ingestion locally, no cluster needed
tests/             pytest suite (no Spark, no Pastas needed)
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


## The Databricks pipeline (meting)

Two tasks, run in order. Permission facts are in `src/tarso_pipeline/paths.py`;
`gold_prd` is **read-only**, everything is written to
`waterkeringbeheer_prd.jvanruitenbeek`.

| # | Notebook | Writes |
|---|---|---|
| 1 | `ingestion/build_manifest.py` | `pastas_series_manifest` (20 longest series) |
| 2 | `transformation/fit_pastas_models.py` | `pastas_fit_metrics` + `.pas` files |

Run task 2 with `limit_series=1` first: the `WRITE VOLUME` grant on `misc_files` is
separate from the table write and is checked with a throwaway file before any fit.

The stress series are **not** fetched at run time — they ship in `data/forcing/`
because the cluster has no outbound internet.
