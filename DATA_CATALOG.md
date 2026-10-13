# Data catalog

Every dataset in the Tarso pipeline: its columns, types and meaning. Written so code
can be written from this file alone.

**Legend**

- *Source* column: **spec** = stated in `DATABRICKS_HANDOFF.md`; **proposed** = a design
  choice of this repo that the spec leaves open (change it freely).
- *pandas* is the dtype as pandas 3.x produces it from `pd.read_csv` / our loaders
  (checked against the code in `src/tarso_pipeline`). *Spark* / *Delta* is the type to
  use when the data is a Spark DataFrame or Delta table.
- Timestamps are timezone-naive. Water levels are **metres relative to NAP**; forcings
  are **millimetres per day**.

## Overview of the data flow

```
head CSV (raw | validated) --read_head_csv--> LoadedHead --prepare_head--> PreparedHead
                                                                              |
forcing CSVs (prec, evap) --load_forcing--> 2 x pd.Series ----> train_tarso --+--> .pas file
                                                                              +--> stats row --> Delta `tarso_runs`
discovery DataFrame (survey, file_path, series_name) drives the distribution.
```

---

## 1. Input: head CSV, "raw" format

One CSV per monitoring well/filter, in a folder per survey. Unvalidated logger export.
Hourly, typically 2-5 years.

| Column | CSV example | pandas | Spark | Nullable | Notes |
|---|---|---|---|---|---|
| `Time` (or `Timestamp`) | `2023-02-02 12:00:00` | `str` -> `datetime64` | `timestamp` | no | Name varies. Candidates matched case-insensitively: `time`, `timestamp`, `datetime`, `date`, `tijd`; otherwise column 0. |
| `head` (name varies, e.g. `Waterniveau`) | `-0.389` | `float64` | `double` | rows with NaN are dropped | Configured head column name if present, otherwise the **first numeric column**. Metres NAP. |

Raw files have no verdict columns and are **never row-filtered**.

## 2. Input: head CSV, "validated" format

Human-reviewed series. Written with a **UTF-8 BOM**, read with `encoding="utf-8-sig"`.
A file is validated only if **all three** of `raw_data`, `gecontroleerd`, `afgekeurd`
are present (case-insensitive, whitespace stripped); a partial match falls back to raw.

| Column | CSV example | pandas | Spark | Nullable | Notes |
|---|---|---|---|---|---|
| `timestamp` | `2025-01-14 14:00:00` | `str` -> `datetime64` | `timestamp` | no | Time column. |
| `raw_data` | `-2.1305` | `float64` | `double` | rows with NaN are dropped | Head in metres NAP. This is the value column. |
| `gecontroleerd` | `1` | `int64` | `int` | treated as not-checked if NaN | 1 = value has been reviewed. |
| `afgekeurd` | `0` | `int64` | `int` | treated as not-rejected if NaN | 1 = reviewer **rejected** the value. |
| `note` | *(usually empty)* | `float64` (all-NaN) or `str` | `string` | yes | Free-text reviewer comment. **Not used** by the pipeline. |

**Row filter (default):** keep `gecontroleerd == 1 and afgekeurd != 1`. Rejected rows are
**dropped, not NaN-filled or interpolated**.

## 3. Input: forcing CSVs (two files, shared by all models)

Daily, millimetres, decades long (e.g. KNMI station 249 Berkhout, from 1951). Passed to
Pastas in mm unchanged. **Column names are pipeline parameters**, not hardcoded.

| File | Column | CSV example | pandas | Spark | Notes |
|---|---|---|---|---|---|
| precipitation | `date` | `1951-01-01` | `str` -> `datetime64` | `date` | Index. Loader finds it via the same time-column candidates. |
| precipitation | `precipitation_mm` (varies) | `0.121` | `float64` | `double` | mm/day. Name set by the `prec_column` parameter. |
| evaporation | `date` | `1951-01-01` | `str` -> `datetime64` | `date` | Index. |
| evaporation | `makkink_mm` (varies, e.g. `evaporation`) | `0.205` | `float64` | `double` | Makkink reference evaporation, mm/day. Set by `evap_column`. |

`load_forcing()` returns a `pd.Series`: `float64`, `DatetimeIndex` (`datetime64[us]`),
sorted, duplicate dates removed (first kept), `name` = the column name.

## 4. In-memory objects (Python, no Spark)

### `LoadedHead` (output of `read_head_csv`)

| Field | Python type | Notes |
|---|---|---|
| `series` | `pd.Series[float64]`, `DatetimeIndex`, name `"head"` | Sorted, no NaN. |
| `format` | `HeadFormat` enum (`"raw"` / `"validated"`) | Detected from columns only. |
| `n_rows_read` | `int` | Data rows in the file. |
| `n_rejected` | `int` | Rows dropped by the verdict filter (0 for raw). |

### `PreparedHead` (output of `prepare_head`)

| Field | Python type | Notes |
|---|---|---|
| `series` | `pd.Series[float64]`, `DatetimeIndex` | Trimmed, resampled, `dropna()`d. Input to Pastas. |
| `skipped` | `bool` | True when validated and fewer than `min_obs` observations. |
| `skip_reason` | `str` | Empty when not skipped. |

## 5. Discovery DataFrame (step 1 of the job)

One row per head CSV; `OBJECT_DATA.csv` is excluded. **Source: spec.**

| Column | pandas | Spark | Nullable | Example |
|---|---|---|---|---|
| `survey` | `str` | `string` | no | `83034-2` (the folder name) |
| `file_path` | `str` | `string` | no | `/Volumes/cat/schema/vol/input_files/83034-2/83034-2_HB017PB01_..._F-264.csv` |
| `series_name` | `str` | `string` | no | `83034-2_HB017PB01_HH0114_34_BUKR_GMW_PB1_F-264` (= `sanitize_name(file stem)`) |

## 6. Output: Delta table `tarso_runs` (one row per series per run)

Append-only; never overwritten in place. Fields in the "Source: spec" rows are required
by section 4 of the handoff; "proposed" rows are the run-identification parameters the
spec asks for without fixing names.

### 6.1 Identity and status

| Column | pandas | Spark / Delta | Nullable | Source | Notes |
|---|---|---|---|---|---|
| `run_id` | `str` | `string` | no | spec | `yy_mm_dd_HH_MM` timestamp, e.g. `26_10_13_14_04`. |
| `series` | `str` | `string` | no | spec | Sanitised file stem = Pastas model name. |
| `model` | `str` | `string` | no | spec | Always `"tarso"`. |
| `status` | `str` | `string` | no | spec | `successful` / `skipped` / `error`. |
| `error` | `str` | `string` | yes | spec | Exception message or skip reason; empty/null when successful. |

### 6.2 Observed data summary

| Column | pandas | Spark / Delta | Nullable | Source | Notes |
|---|---|---|---|---|---|
| `first_entry` | `str` | `string` | yes | spec | `YYYY-MM-DD`, first observation. |
| `last_entry` | `str` | `string` | yes | spec | `YYYY-MM-DD`, last observation. |
| `length_series` | `int` | `int` | yes | spec | Observations **used** (after resampling). For skips: the count that triggered the skip. |
| `min_value` | `float` | `double` | yes | spec | Observed head minimum, m NAP. |
| `max_value` | `float` | `double` | yes | spec | Observed head maximum, m NAP. |

### 6.3 Fit statistics

From `ml.stats.summary()`, a DataFrame indexed by metric name with a single column
`"Value"`; pulled by index label, **`None` when absent**. All null for skipped/error rows.

| Column | pandas | Spark / Delta | Nullable | Source | Meaning |
|---|---|---|---|---|---|
| `rmse` | `float` | `double` | yes | spec | Root mean squared error [m]. |
| `sse` | `float` | `double` | yes | spec | Sum of squared errors [m²]. |
| `mae` | `float` | `double` | yes | spec | Mean absolute error [m]. |
| `nse` | `float` | `double` | yes | spec | Nash-Sutcliffe efficiency (healthy: ~0.7-0.95). |
| `evp` | `float` | `double` | yes | spec | Explained variance percentage. |
| `rsq` | `float` | `double` | yes | spec | R² (healthy: ~0.7-0.95). |
| `kge` | `float` | `double` | yes | spec | Kling-Gupta efficiency. |
| `bic` | `float` | `double` | yes | spec | Bayesian information criterion. |
| `aic` | `float` | `double` | yes | spec | Akaike information criterion. |
| `aicc` | `float` | `double` | yes | spec | Corrected AIC. |
| `response_tmax_95` | `float` | `double` | yes | spec | `ml.get_response_tmax("Tarso", cutoff=0.95)` [days]; `None` on failure. |

### 6.4 Calibration and processing parameters

| Column | pandas | Spark / Delta | Nullable | Source | Notes |
|---|---|---|---|---|---|
| `tmin_cal` | `str` | `string` | yes | spec | Calibration start actually used, `YYYY-MM-DD`. |
| `tmax_cal` | `str` | `string` | yes | spec | Calibration end actually used, `YYYY-MM-DD`. |
| `resample_rule` | `str` | `string` | no | spec | e.g. `"D"`. |
| `resample_method` | `str` | `string` | no | proposed | e.g. `"mean"`. |
| `head_format` | `str` | `string` | yes | proposed | `raw` / `validated` as detected. |
| `n_rejected` | `int` | `int` | yes | proposed | Rows dropped by the validation filter. |

### 6.5 Run identification (spec asks for these; names proposed)

| Column | pandas | Spark / Delta | Nullable | Notes |
|---|---|---|---|---|
| `survey` | `str` | `string` | no | Survey folder name. |
| `prec_file` | `str` | `string` | no | Precipitation forcing file name. |
| `prec_column` | `str` | `string` | no | Column used. |
| `evap_file` | `str` | `string` | no | Evaporation forcing file name. |
| `evap_column` | `str` | `string` | no | Column used. |
| `validation_filter` | `bool` | `boolean` | no | Whether the verdict filter was applied. |
| `min_obs` | `int` | `int` | no | Minimum observations (default 100, validated only). |
| `tmin_cal_param` | `str` | `string` | yes | Requested calibration start (parameter), null if none. |
| `tmax_cal_param` | `str` | `string` | yes | Requested calibration end (parameter), null if none. |
| `pastas_version` | `str` | `string` | no | e.g. `1.12.0`. |
| `model_path` | `str` | `string` | yes | Path of the `.pas` file; null when not successful. |

## 7. Output: model files

| Item | Value |
|---|---|
| Format | Pastas `.pas` (JSON), written with `ml.to_file(path)`, reloaded with `ps.io.load(path)` |
| Name | `{series}_tarso_model.pas` |
| Location | Unity Catalog Volume: `/Volumes/<catalog>/<schema>/<volume>/models/...` (not DBFS root, not driver-local disk) |
| Alternative | `ml.to_dict()` as JSON string in a Delta `string` column |

## 8. Job parameters (all configurable)

| Parameter | Type | Default | Notes |
|---|---|---|---|
| `input_volume_path` | string | none | Root containing survey folders. |
| `surveys` | list[string] | none | Survey folder(s) to process. |
| `prec_file`, `evap_file` | string | none | Forcing CSV paths. |
| `prec_column`, `evap_column` | string | none | Forcing column names. |
| `head_column` | string | `head` | Preferred raw value column; falls back to first numeric. |
| `resample_rule` | string | `D` | pandas offset alias. |
| `resample_method` | string | `mean` | One of `mean, median, min, max, first, last, sum`. |
| `validation_filter` | bool | `true` | Apply `gecontroleerd == 1 & afgekeurd != 1`. |
| `min_obs` | int | `100` | Validated files only. |
| `tmin_cal`, `tmax_cal` | string (date) | none | Optional calibration window. |
| `run_id` | string | `yy_mm_dd_HH_MM` now | Identifies the run. |
| `output_volume_path` | string | none | Where `.pas` files go. |
| `output_table` | string | `tarso_runs` | Fully qualified `catalog.schema.table`. |

## 9. Fixed model constants (from the spec)

| Item | Value |
|---|---|
| Stress model name | `Tarso` |
| `ps.Model(..., constant=False)` | no constant |
| `TarsoModel` `dmin` / `dmax` | per series: `head.min()` / `head.max()` |
| `Tarso_a0` bounds | `pmin=0.0`, `pmax=200.0` |
| `Tarso_a1` bounds | `pmin=10.0`, `pmax=200.0` |
| Solve window | `tmin`/`tmax` as `YYYY-MM-DD` strings, clipped to the head extent |
| `report` | `False` |
| `matplotlib` backend | `Agg` if imported at all |
