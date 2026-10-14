# tarso-pipeline

Minimal Pastas/Tarso training pipeline for Databricks: load head (groundwater level)
CSVs plus two meteorological forcing series, train one Pastas `TarsoModel` per head
series, persist the model and its fit statistics.

## Layout

```
src/tarso_pipeline/
  meting.py        SQL over the meting table: verdicts, manifest, duplicates
  clean.py         collapse duplicate timestamps + resample; counts recorded
  fit.py           fit_series: load -> clean -> fit -> save -> metrics row
  fit_metrics.py   the pastas_fit_metrics row, read from the Pastas API
  knmi.py          KNMI forcing download (precipitation + Makkink)
  forcing.py       load the forcing CSV (units, station) + warmup coverage
  forcing_plot.py  annual totals and the check chart
  io.py            load_forcing / read_head_csv
  naming.py        sanitize_name
  paths.py         repo_root, forcing_path, and the Databricks write targets
notebooks/
  exploration/explore_meting.py        what is in the source, how much is usable
  ingestion/build_manifest.py          JOB TASK 1 -> pastas_series_manifest
  transformation/fit_pastas_models.py  JOB TASK 2 -> pastas_fit_metrics + .pas
  analysis/inspect_results.py          read the output tables after a run
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
| `ingestion/build_manifest.py` | pick the 20 longest series; quantify duplicate timestamps | **task 1** | `pastas_series_manifest` |
| `transformation/fit_pastas_models.py` | clean, fit, save `.pas`, record metrics | **task 2** | `pastas_fit_metrics`, `.pas` files |
| `analysis/inspect_results.py` | did it work, are the fits any good, Tarso vs baseline | no | nothing |

Task 2 depends on task 1: it reads the manifest table task 1 writes.

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

**`validatie_status` is ignored.** By decision: every row with a `waarde` is used,
whatever its review status. Revisit once the status values are understood.

**Forcing units.** The fit notebook converts mm to **metres per day** by default
(`forcing_units=m`), matching the head in metres NAP. The original repo passed
millimetres straight through; the choice only rescales the gain parameters, but it
makes the two conventions incomparable, so it is recorded on every metrics row.
