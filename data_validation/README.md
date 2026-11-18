# Groundwater data validation

This project:
1. Copies the groundwater head measurements from the Databricks source.
2. Validates them, first with automated checks and then by a person in a review app.
3. Writes the approved hours back to Databricks as a Delta table, `validated_meting`.

The Pastas models (`data_basis=validated`) and the monitoring dashboard read that table. **The source is never touched**: `gold_prd` is read-only, and everything we write goes to `waterkeringbeheer_prd.jvanruitenbeek`.

```
gold_prd.hl_grondwaterpeilmeting (meting, meetreeks)  +  gold_prd.hl_cs_objecten.meetlocatie
   │  ① RETRIEVE: read, clean to hourly, match locations        databricks_code/notebooks/01
   ▼
waterkeringbeheer_prd.jvanruitenbeek.source_copy_meetreeks / source_copy_meting
   │  ② export per-series CSVs into the Git folder               databricks_code/notebooks/02
   ▼
GitHub ──pull──► laptop: input_output/output_databricks/
                   │  ③ VALIDATE: automated flags v1–v4, rules A/B     local_validation/validate.ipynb
                   │  ④ REVIEW: a person approves / rejects / corrects  streamlit_app.py (or the central app)
                   ▼
GitHub ◄──push── input_output/output_repository/  (the review stores)
   │  ⑤ BACK INTO THE WAREHOUSE: approved hours → Delta          databricks_code/notebooks/03
   ▼
waterkeringbeheer_prd.jvanruitenbeek.validated_meting  ──► Pastas, monitoring dashboard
```

## 1. How we retrieve the data

**`01_source_to_personal`** (Databricks) reads the source and writes our own copy.

- **What it reads:**
  - `meetreeks`: one row per sensor series, with code, supplier (`bronsysteem`), filter depths and top of well;
  - `meting`: the raw measurements, about 55 per day per series;
  - `hl_cs_objecten.meetlocatie`: the location registry, with coordinates, name and compartment.
- **Location match:** each series is linked to its `meetlocatie` with four keys, tried in order; the first that hits wins (`source_read.CS_MATCH_KEYS`):
  1. `meetlocatie_object_id = l.id`;
  2. `meetlocatie_code = l.code`;
  3. `trim(code) = l.code`;
  4. `object_id = l.id`.

  It is a LEFT join, so unmatched series stay in, with `matched = 'no match found in cs_objecten'`. The key that hit is in `match_key`.
- **Cleaning:** in Spark SQL, on the UTC timestamp `tijdstempel_utc`. Each value is floored to the hour, and the values within an hour are averaged. The local `tijdstempel` repeats an hour every October, so it is not used.
- **What it writes:**
  - `source_copy_meetreeks`: every `meetreeks` column plus the match;
  - `source_copy_meting`: hourly `meetreeks_id, vendor, timestamp, head`.

**`02_export_to_git`** (Databricks) turns that copy into files in the Git folder:

```
input_output/output_databricks/time_series_data/<filename>.csv   Time,head: the hourly copy per series
input_output/output_databricks/metadata/object_data.csv          one row per series (join on filename)
```

`transport=write` (the default) only writes the files; you commit and push from the Git-folder UI. `transport=git` pushes with a PAT, and `transport=volume` copies to the volume for a laptop to fetch. See [`databricks_code/README.md`](databricks_code/README.md).

`00_probe_env` checks the grants and whether a push would work before a real run.

## 2. How we validate it automatically

**`local_validation/validate.ipynb`** runs locally on the export, in about 40 s for about 700 series. The logic is plain pandas in [`local_validation/checks.py`](local_validation/checks.py), and the thresholds are in [`config/settings.yaml`](config/settings.yaml). The monitoring job runs the same function (`validate_series`), so the dashboard counts exactly what the review app flags.

### Flag layers

The flag layers run in order on a continuous hourly grid, so a step is never measured across a gap. Each layer keeps the original value of what it flags in its own column and blanks it, so the next layer sees only what is left:

| | check | default | why |
|---|---|---|---|
| `v1` | out of bounds: below the filter bottom or above the top of the well | `eind_filter` / `bovenkant_peilbuis` of the series | a head outside the tube cannot be measured there |
| `v2` | unrealistic step between two hours | up > +0.30 m, down < −0.05 m | groundwater does not jump; a jump is a sensor moved or a logger reset |
| `v3` | at the filter bottom: within 0.15 m of `eind_filter` | e.g. with `eind_filter` −4.10, −3.95 and −4.05 are flagged and −3.92 is not | the sensor is probably dry, hanging at its floor |
| `v4` | statistical outlier (Tukey) | outside Q1 − 1.5·IQR … Q3 + 1.5·IQR of the series | spikes |

### Autoflag rules

These rules work on the raw value and act on the review store:

| | rule | default |
|---|---|---|
| `rule_a` | drop ≥ 0.10 m in one hourly step | **report-only**: it disagreed with human reviewers on most hits |
| `rule_b` | value ≤ filter bottom + 0.10 m | **applied**: the hour is set to rejected (`afgekeurd = 1`) with note `auto: rule B`, awaiting a reviewer's confirmation |

An automated rule never touches an hour a person has reviewed (`apply_autoflags`).

### The three data layers

| layer | folder | git | role |
|---|---|---|---|
| 1. source copy | `input_output/output_databricks/` | tracked | the exact export; written by Databricks **only** |
| 2. derived | `output_data/validated/`, `output_data/validation_summary.csv` | ignored | flags per hour; disposable, rebuilt each run |
| 3. review store | `input_output/output_repository/<filename>.csv` | tracked | **the asset**: the person's decisions. Columns `timestamp, raw_data, gecontroleerd, afgekeurd, note, correctie` |

- **New exports:** a new export overwrites layer 1, and layer 2 is rebuilt. Layer 3 is **merged into, never replaced**: reviewed hours keep their verdict and note, and new hours arrive unreviewed.
- **Guard:** every write goes through `data_io.assert_writable()`, which refuses any path inside layer 1.
- **Naming:** the source copy and the store join on the filename stem, so never rename one without the other.

## 3. Manual review

The review app is in the central app under *Data validation*, or standalone with `streamlit run streamlit_app.py`:

- **Map:** every series with a position, coloured by supplier or by review progress; click a well to open its series.
- **Review:**
  - Opening a series merges the latest export into its store and applies rule B.
  - The plot colours every hour by status: gray unreviewed, green approved, red rejected, pink auto-rejected awaiting confirmation. It shows the flags as markers, plus the filter bottom with the v3 band and the top of the well.
  - In the table, tick *gecontroleerd* (reviewed) or *afgekeurd* (rejected) per hour, or approve/reject everything visible. Notes are at most 30 characters.
  - **Save** writes the store atomically.
- **Correction:** one offset in metres for every hour in a period, e.g. +0.10 m for the months a sensor hung lower after maintenance.
  - It is stored per hour in `correctie`; `raw_data` is never changed.
  - Applying again replaces the offset; it does not add to it.
  - The flags still run on the raw values.
- **Data sources:** per series, which fields come from the API (blue) and which from `hl_cs_objecten` (green). An empty green cell is a link still to make.
- **Overview:** progress per supplier and per series.

Saving is off in the deployed Databricks App (`VALIDATION_READ_ONLY=1`), because an app's files are not kept. Review locally, then commit and push `input_output/output_repository/`.

## 4. How it is pushed into the warehouse as a Delta table

**`03_validated_to_delta`** (Databricks, after you pull the pushed review) builds `validated_meting`:

1. It reads every store in `input_output/output_repository/` and finds its `meetreeks_id` through `object_data.csv`. An older nested layout `<vendor>/<origin>/<series>.csv` is matched through `source_copy_meetreeks`.
2. It keeps the **approved** hours: `gecontroleerd = 1` and `afgekeurd ≠ 1`. Unreviewed and rejected hours are left out.
3. It writes them **in the shape of the source `meting`**, so a reader only has to change the table name:

| column | as in `meting` | value |
|---|---|---|
| `meetreeks_id` | yes | the series key |
| `tijdstempel_utc` | yes | the hour, UTC (the store's key) |
| `tijdstempel` | yes | the same hour, Europe/Amsterdam wall clock |
| `waarde` | yes | the **validated** head = `raw_data + correctie`, m NAP |
| `validatie_status` | yes | `2` (*Valide*) |
| `waarde_ruw`, `correctie` | no | the hourly source value and the offset |
| `note`, `vendor`, `filename`, `run_id`, `validated_at` | no | provenance |

The table is **overwritten** each run, because the stores already hold the complete current picture. It is partitioned by `vendor`, and earlier versions stay readable with Delta time travel (`VERSION AS OF`). The values are hourly means, not the source's raw ~55 rows a day.

## Run order

| step | where | what |
|---|---|---|
| ① copy | Databricks | `00` → `01` → `02`, then commit and push the export from the Git-folder UI |
| ② validate | laptop | `git pull`, run `local_validation/validate.ipynb` |
| ③ review | laptop | review app, **Save**, then commit and push `input_output/output_repository/` |
| ④ load | Databricks | pull, run `03_validated_to_delta` |
| ⑤ use | Databricks | the Pastas notebooks with `data_basis=validated`; the monitoring job the next morning |

```bash
git pull
python -m pytest tests                       # checks, merge contract, layer guard, store sync
jupyter lab local_validation/validate.ipynb  # flags + store sync
streamlit run streamlit_app.py               # review → input_output/output_repository/
git add input_output/output_repository && git commit -m "review" && git push
```

Start with `limit_series=1` on `01` for a first run. Confirm the round trip, then set it to `0` (all series).

## Layout

| path | what |
|---|---|
| `databricks_code/notebooks/` | `00_probe_env`, `01_source_to_personal`, `02_export_to_git`, `03_validated_to_delta` |
| `databricks_code/lib/` | `source_read.py` (SQL, cs match, cleaning, naming), `git_sync.py` (push, volume fallback) |
| `databricks_code/jobs/workflow_config.json` | a job definition for `00 → 01 → 02` |
| `local_validation/` | `validate.ipynb`, `checks.py` (v1–v4, rules A/B), `data_io.py` (layers, paths, guard), `stores.py` (open, merge, sync, save), `validation_store.py` (the frozen store contract), `knmi_forcing.ipynb` |
| `streamlit_app.py`, `review_app/` | the review app: map, review, overview, data sources |
| `config/settings.yaml` | paths, thresholds, autoflag switches, `notes_max_len` |
| `input_output/knmi_berkhout.csv`, `data/forcing/` | hourly KNMI forcing for the rain overlay in the review plot |
| `tests/` | `python -m pytest tests` |
| `docs/` | Databricks background: source schema (`DATABRICKS_HANDOFF.md` §2.0), grants (`DATABRICKS_PERMISSIONS.md`), the location registry (`DATABRICKS_layer_cs_objecten.md`) |

Not ported from on-prem:
- *Generate Data*: replaced by the Databricks export.
- *Sensor Stitching*: each Databricks series is already one `meetreeks`.
- The return-period plots: these are now in the Pastas pipeline.
