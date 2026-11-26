# Pastas pipeline configuration

| File | What | Read by |
|---|---|---|
| `model_settings.toml` | Settings that hold for **every** series: the candidate model types, holdout share, minimum span, the selection thresholds and the soil-moisture bucket. | `tarso_pipeline.settings`, so `scripts/run_local.py` and the notebooks `fit_pastas_models`, `run_extremes` (the model selection) and `export_results` |
| `series_overrides.csv` | Exceptions for **one** series that the data cannot show: a stress that does not apply there, or extremes not to trust. | `tarso_pipeline.overrides`: skipped in the fit, never picked by the selection |
| `dev.yaml`, `prod.yaml` | Older environment settings (catalog, mounts), not used by the pipeline code. | – |

Both files are in git, so every change has an author, a date and a diff. A change to
`model_settings.toml` applies on the next run. On Databricks, pull the repo first.

## Changing a setting

- **Selection thresholds** (`[selection]`) need no refit. Locally, `python scripts/run_local.py --reselect <run folder> --export` redoes the selection of a finished run in a few minutes. On Databricks, rerun `run_extremes` (or only its selection cell) and `export_results`.
- **Model types, holdout, `min_days`, the bucket** (`[fit]`, `[bucket]`) change the fits, so they need a full run.
- A value of `"off"` switches an optional rule off. A key left out takes the built-in default, which is what the pipeline did before the file existed.

## `series_overrides.csv`

One row per series; the key is `meetreeks_id`. `series_name` is only there for people.

| Column | Values | Effect |
|---|---|---|
| `exclude_stresses` | `;`-separated: `lake`, `pressure`, `bucket`, `radar` | The model types with that stress (or with radar precipitation) are not fitted for this series and never picked. |
| `exclude_model_types` | `;`-separated model types, e.g. `flex_exp_lake_baro_bucket_radar` | These types are not fitted and never picked. |
| `trust_extremes` | `nee` (also `no`, `false`, `0`); empty = yes | Return periods are still computed. The pick's reason in the app says "extremen niet betrouwbaar: \<note\>". |
| `note` | free text | Why. Always fill it in. |
| `added_by`, `added_at` | name, date | Who and when. |

Example row:

```csv
meetreeks_id,series_name,exclude_stresses,exclude_model_types,trust_extremes,note,added_by,added_at
c2d4ccd1...,series_c2d4ccd19f6dd8e691f242fc,lake,,nee,onttrekking naast de peilbuis; damwand houdt het meer tegen,J. Jansen,2026-11-26
```

A wrong value (an unknown stress or model type, two rows for one series) stops the run
with an error that names the row, instead of being silently ignored.

This is not the model **choice** (`pastas/model_choice/`, where a person picks one model in
the app). An override narrows what the automatic selection may choose from. A person's
choice still wins over both.
