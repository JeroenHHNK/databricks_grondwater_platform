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
notebooks/
  ingestion/knmi_forcing.py   KNMI -> Delta, incremental
scripts/
  check_knmi.py    run the ingestion locally, no cluster needed
tests/             pytest suite (no Spark, no Pastas needed)
```

Planned next steps: `train.py` (Tarso fit + stats row), `spark.py` (discovery +
`mapInPandas`), `notebooks/run_tarso.py` (the Databricks Job entry point).

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

## Databricks

Add this repo as a **Git folder** (Workspace > Create > Git folder). In a notebook or
job task:

```python
%pip install -e /Workspace/Repos/<user>/<repo>      # or build a wheel and attach it
```

Source files are never renamed: model names are derived from file stems with
`sanitize_name`. Head files are read from Unity Catalog Volume paths
(`/Volumes/<catalog>/<schema>/<volume>/...`).
