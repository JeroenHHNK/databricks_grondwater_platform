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

## 3a. Source: Delta table `knmi_daily_forcing` (**proposed**)

The forcing CSVs of section 3 can also be produced from KNMI directly. Written by
`notebooks/ingestion/knmi_forcing.py` via `src/tarso_pipeline/knmi.py`; column names
match section 3 so `load_forcing()` reads an export of this table unchanged.

**Natural key `(station, date)`**, upserted with a `MERGE` — re-running never duplicates.

| Column | pandas | Spark / Delta | Nullable | Notes |
|---|---|---|---|---|
| `station` | `Int64` | `int` | no | KNMI station number. Default **249 Berkhout**, the station named in section 3. |
| `date` | `datetime64[ns]` | `date` | no | Observation day. |
| `precipitation_mm` | `float64` | `double` | yes | Daily sum, mm. KNMI `RH` ÷ 10. |
| `makkink_mm` | `float64` | `double` | yes | Makkink reference evaporation, mm. KNMI `EV24` ÷ 10. |
| `ingested_at` | — | `timestamp` | no | When the row was last written. Not part of the key. |

**Source API.** `POST https://www.daggegevens.knmi.nl/klimatologie/daggegevens` with
`start`/`end` as `YYYYMMDD`, `vars=RH:EV24`, `stns=<station>`. The response is CSV:
comment lines prefixed `#`, the last of which (`# STN,YYYYMMDD,   RH, EV24`) carries the
column names in KNMI's own order — so the header is parsed, never assumed.

**Conversions applied on ingest.**

| KNMI | Meaning | Stored as |
|---|---|---|
| `RH` / `EV24` in 0.1 mm | — | ÷ 10 → mm |
| `RH = -1` | measured, below 0.05 mm | `0.0` (a dry day, **not** NULL) |
| blank field | missing | `NULL` |

**Incremental refresh.** `max(date)` for the station is the watermark; the next window
is `[watermark + 1 − lookback_days, today]`, clamped to `backlog_start` (2020-01-01).
When the table is current the window is empty and **no HTTP request is made**. The
`lookback_days` overlap (default 2) re-fetches recently published days, which KNMI
revises as they are validated.

**Publication lag.** KNMI runs 1–2 days behind, so a run on day *D* normally stores
through *D−2*. The notebook's coverage check compares stored days against
`datediff(max, min) + 1`; a non-zero gap is a genuinely missing KNMI day, which matters
because Pastas wants an unbroken daily forcing series.

**Committed forcing file `data/forcing/knmi_forcing_<station>.csv`.** The cluster has no
outbound internet, so the fetch runs locally and the result is **committed to the repo**;
a Git folder pull delivers it. Written by `write_export`, read by `read_export` (for the
`MERGE`) or by `load_forcing` (for training). Columns are exactly the four above
**without `ingested_at`**, dates ISO `YYYY-MM-DD`, missing values empty; ~58 KB for
2020-to-date. Locate it with `tarso_pipeline.paths.forcing_path(station)` rather than a
hardcoded `/Workspace/...` path.

One file carries both variables — `load_forcing(path, column)` selects which — so
section 3's "two files" is satisfied by two calls on one file:

```python
precipitation = load_forcing(forcing_path(249), "precipitation_mm")
evaporation   = load_forcing(forcing_path(249), "makkink_mm")
```

`read_export` returns the same frame `fetch_daily` would, so the ingestion notebook's
`MERGE` is one code path for all three `source` modes (`repo` / `api` / `file`) and
re-merging stored rows is a no-op.

**Decisions recorded 2026-10-14.** Head series run from 2022 onwards, so KNMI station
249 (from 1999-03-23) provides ~8300 days before them and the full 3650-day warmup fits.
`validatie_status` is **not** used as a filter: every row with a `waarde` is fitted,
whatever its review status. Series are named `series_<first 24 hex of meetreeks_id>`; the full id stays on every
metrics row, so a downstream join on the prefix recovers the rest.

Artefacts are written per run, grouped by kind rather than by series:

```
/Volumes/waterkeringbeheer_prd/misc_files/misc_files/pastas_pipeline/
    run_yy_mm_dd/
        pas_files/          series_<hash24>_<model>.pas
        results_files/      series_<hash24>_<model>_results.png
        diagnostics_files/  series_<hash24>_<model>_diagnostics.png
```

The model type is in each filename because the folders are shared across series, so
without it a `recharge` run would overwrite the `tarso` one. All three paths are
recorded on the metrics row (`model_path`, `plot_path`, `diagnostics_path`).
Two runs on the same day share a folder and overwrite each other's artefacts while
keeping both metrics rows; the `run_folder` widget overrides the name to avoid that.

> **Hourly is not an option for evaporation.** KNMI's `uurgegevens` product has no
> Makkink: its `PRCP` set is `DR:RH` (duration + hourly sum). `EV24` is daily-only.

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
| `span_days` | `int` | `int` | yes | proposed | Calendar days from first to last observation. What the `min_days` filter tests. |

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
| `min_obs` | `int` | `int` | no | Minimum observations (default 100, **validated files only**). |
| `min_days` | `int` | `int` | no | Minimum span in days (default 365, **every format**). 0 disables. |
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
| `min_obs` | int | `100` | Minimum observations. Validated files only. |
| `min_days` | int | `365` | Minimum series span in days. Applies to every format; 0 disables. A Tarso fit needs a full seasonal cycle. |
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


## 10. Stage 3: extremes and return periods (**proposed**)

Drives each calibrated `.pas` with the 16 IMPREX ensemble members and reports return
levels. Written by `notebooks/transformation/run_extremes.py`, into the **same dated
run folder** as the models it read.

### Identity

`series_name` is the truncated name the `.pas` filename carries; `series_id` is the
**full 64-character `meetreeks_id`**, recovered by joining `pastas_fit_metrics` on
`series_name`. A model with no metrics row falls back to the truncated name rather
than being dropped, and the summary says how many fell back.

### The pooled sample

16 members x 44 hydrological years (April-March, 1956-1999) = **704 blocks per tail**.
800 means the partial-block filter is not running; 16 440 means the members were
concatenated in time instead of pooled at the extremes.

### Trimming

`exclude_first` defaults to **450**, leaving 254 of 704 (`lam = 0.361`). A T-year
event is then the `1 / (lam * T)` quantile, not `1 / T`. **The smallest computable
return period is `1 / lam = 2.77`, so T=2 is reported as null** rather than invented.

### Artefacts, inside `run_yy_mm_dd/`

| Path | Contents |
|---|---|
| `csv/<series>_return_periods.csv` | T x {`dr_`,`wet_`} x {`exp`,`gpd`,`gev`,`emp`}, metres NAP |
| `pooled_extremes/<series>_{dry,wet}.csv` | every pooled extreme with `member`, `year`, date |
| `used_extremes/used_extremes_<series>.csv` | each extreme with rank and a `used` flag |
| `return_plots_fitted/`, `qq_plots/`, `confidence_plots/` | one PNG per model |
| `evt_selection.csv` | chosen family per tail, with shape, SE, verdict, review flag |
| `tail_parameters.csv` | shape/loc/scale per family per tail |
| `<run>_extremes_summary.csv` | one row per model: the run's report card |
| `bias_correction_run_log.json` | copied from the IMPREX folder: how the forcing was corrected |

Roll-ups also append to `waterkeringbeheer_prd.jvanruitenbeek.pastas_evt_selection`
and `.pastas_extremes_summary`.

### Reading the result

`gpd` is fitted and reported but **never recommended**: a generalised Pareto belongs
on threshold exceedances, not block maxima. Quote **T=10** (stable to centimetres) and
**T=100** (to ~0.1 m). **T=1000 is extrapolation**: 704 blocks reach an empirical
return period of ~705 years, so the families disagree there by 0.3 m and sometimes
much more.

A `degenerate` verdict on a wet tail means the model's `dmax` ceiling is capping the
head, not that the fit failed.
