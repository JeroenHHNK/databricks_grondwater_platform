# tarso-pipeline

Minimal Pastas/Tarso training pipeline for Databricks: load head (groundwater level)
CSVs plus two meteorological forcing series, train one Pastas `TarsoModel` per head
series, persist the model and its fit statistics.

## Layout

```
src/tarso_pipeline/
  naming.py      sanitize_name: file stem -> valid Pastas model name
  io.py          read_head_csv (raw/validated detection), load_forcing
  preprocess.py  prepare_head: trim, resample, dropna, min_obs skip
tests/           pytest suite (no Spark, no Pastas needed)
```

Planned next steps: `train.py` (Tarso fit + stats row), `spark.py` (discovery +
`mapInPandas`), `notebooks/run_tarso.py` (the Databricks Job entry point).

## Local development

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"          # add ",pastas" once train.py lands
pytest
```

## Databricks

Add this repo as a **Git folder** (Workspace > Create > Git folder). In a notebook or
job task:

```python
%pip install -e /Workspace/Repos/<user>/<repo>      # or build a wheel and attach it
```

Source files are never renamed: model names are derived from file stems with
`sanitize_name`. Head files are read from Unity Catalog Volume paths
(`/Volumes/<catalog>/<schema>/<volume>/...`).
