# Handoff: minimal Pastas/Tarso training pipeline on Databricks

You are being asked to rebuild a **stripped-down** version of an existing on-premises
Python repository (`pastas_machine`) as a Databricks pipeline. This document is the
complete specification — you do not have access to the original repo, and you do not
need it.

**Scope: load head (groundwater level) CSV files + two meteorological forcing series,
train one Pastas `TarsoModel` per head series, persist the model and its fit statistics.
Nothing else.**

---

## 1. Domain concept in one paragraph

A groundwater monitoring well (a "piezometer", Dutch *peilbuis*) logs water level
("head", in metres relative to Dutch ordnance datum NAP) hourly for typically 2–5 years.
[Pastas](https://pastas.readthedocs.io/) is a Python library for **time series models of
groundwater head**: it fits a transfer-function-noise model that explains the observed
head from meteorological stresses — precipitation and (Makkink reference) evaporation.
A `TarsoModel` (Threshold AutoRegressive Self-exciting Open-loop) is a specific
non-linear Pastas stress model: two linear reservoirs with different drainage levels and
resistances, switching at a threshold. Physically that represents a well which drains to
a ditch above a certain level and only to the deeper subsurface below it. That
non-linearity is why Tarso is the model type of interest here.

The pipeline is **embarrassingly parallel**: each head CSV is an independent model fit.
There are hundreds to thousands of series, each fit taking seconds to a few minutes.
That is the thing worth distributing on Databricks.

---

## 2. Input data contract

### 2.1 Head series (one CSV per monitoring well / filter)

Organised as **one folder per survey project**, each folder containing many CSVs.
Folder names look like `83034-2`, `87074-1`, `4424_Hoorn_Zuiderdijk`. File names look
like:

```
83034-2_HB017PB01_HH0114_34_BUKR_GMW_PB1_F-264.csv
```

Two column conventions exist and **must be detected from the columns, never from the
filename or folder**:

**(a) "raw"** — an unvalidated logger export: a timestamp column plus one value column.

```csv
Time,head
2023-02-02 12:00:00,-0.389
2023-02-02 13:00:00,-0.387
```

The time column may also be called `Timestamp`, and the value column name varies
(`head`, `Waterniveau`, …). Rule: use the configured head column name if present,
otherwise **the first numeric column**.

**(b) "validated"** — a human-reviewed series carrying the reviewer's verdict per row.

```csv
timestamp,raw_data,gecontroleerd,afgekeurd,note
2025-01-14 14:00:00,-2.1305,1,1,
2025-01-14 15:00:00,-1.6180,1,0,
```

- `gecontroleerd` = 1 → the value has been reviewed
- `afgekeurd` = 1 → the reviewer **rejected** the value
- `note` = free-text comment, usually empty

A file counts as validated only when **all three** of `raw_data`, `gecontroleerd`,
`afgekeurd` are present; a partial match falls back to raw. Validated files are written
with a UTF-8 BOM, so read them with `encoding="utf-8-sig"` or column matching breaks on
a leading `﻿`. Match column names case-insensitively and strip whitespace.

Default row filter for validated files: **keep `gecontroleerd == 1 & afgekeurd != 1`**.
Rejected rows are **dropped, not NaN-filled** — Pastas handles irregular observation
series with gaps, so nothing is interpolated, backfilled or reindexed to close them. Raw
files have no verdict columns and are never filtered.

### 2.2 Forcing series (shared by all models)

Two CSVs, a date index plus one value column, **daily, in millimetres**:

```csv
date,precipitation_mm
1951-01-01,0.121
1951-01-02,0.000
```

```csv
date,makkink_mm
1951-01-01,0.205
1951-01-02,0.310
```

These come from a KNMI weather station (e.g. station 249, Berkhout) and span decades —
far longer than any head series. Column names are **not** stable across files
(`precipitation_mm`, `makkink_mm`, `evaporation`, …), so the column name must be a
pipeline parameter, not hardcoded. The original repo passes the millimetre series
straight into Pastas, which only rescales the gain parameters; keep that behaviour so
results stay comparable.

### 2.3 Filename constraint (important)

**Pastas model names cannot contain spaces, `+`, `.`, brackets or other punctuation.**
The source files arrive with names like
`83034-2 HB017PB01 HH0114+34_BUKR_GMW_PB1_F-264.csv`. The repo has a pre-step that
renames them on disk with `re.sub(r"[^a-zA-Z0-9_-]", "_", stem)`, collapsing repeated
underscores and stripping leading/trailing ones. In Databricks, **do not rename source
files**; derive the model name by applying that same sanitisation to the file stem. The
model name is the series identifier everywhere downstream.

---

## 3. The core transformation — per-series Tarso training

This is the heart of the pipeline. Reproduce it faithfully. Reference implementation,
condensed from the original `create_tarso_model`:

```python
import pandas as pd
import pastas as ps   # pinned at 1.12.x in the original repo

def train_tarso(series_name: str, head: pd.Series,
                prec: pd.Series, evap: pd.Series,
                tmin_cal=None, tmax_cal=None):
    # head must have a DatetimeIndex, be sorted, and contain no NaN
    if head.first_valid_index() is None:
        raise ValueError(f"No valid head observations in {series_name}")
    head = head.loc[head.first_valid_index():head.last_valid_index()]

    head_min, head_max = head.index.min(), head.index.max()
    tmin = max(pd.to_datetime(tmin_cal), head_min) if tmin_cal else head_min
    tmax = min(pd.to_datetime(tmax_cal), head_max) if tmax_cal else head_max

    # constant=False: TarsoModel brings its own drainage levels, so a Pastas
    # constant would be redundant and unidentifiable.
    ml = ps.Model(head, name=series_name, constant=False)

    sm = ps.TarsoModel(prec, evap,
                       dmin=head.min(), dmax=head.max(),
                       name="Tarso")
    ml.add_stressmodel(sm)

    # Drainage-resistance bounds [days]. Without them the solver wanders into
    # physically meaningless territory and fits fail or become unstable.
    ml.set_parameter("Tarso_a0", pmin=0.0,  pmax=200.0)
    ml.set_parameter("Tarso_a1", pmin=10.0, pmax=200.0)

    ml.solve(tmin=tmin.strftime("%Y-%m-%d"),
             tmax=tmax.strftime("%Y-%m-%d"),
             report=False)

    stats = ml.stats.summary()   # DataFrame indexed by metric name, column "Value"
    return ml, stats
```

Note that `dmin`/`dmax` are taken from the observed head range of that specific series —
they are per-series, not global constants.

Pre-processing applied to the head series **before** this call:

1. Load, format-detect, and (validated only) row-filter as in §2.1.
2. Trim to the first/last valid observation.
3. **Resample to daily means** (`resample("D").mean()`), then `dropna()` — resampling an
   observation series with gaps creates empty buckets, and Pastas must not receive NaN
   rows. Rule and method are parameters (`"D"` / `"mean"` by default).
4. **Skip the series** if fewer than `min_obs` observations survive (default **100**),
   for validated files only. Record the skip as a row in the report with
   `status="skipped"` — a skipped series must not silently disappear.

---

## 4. Output contract

Per series, record **one row** with at least:

| field | meaning |
|---|---|
| `series` | sanitised file stem (= Pastas model name) |
| `model` | `"tarso"` |
| `status` | `successful` / `skipped` / `error` |
| `error` | exception message, empty when successful |
| `first_entry`, `last_entry` | observed head extent (`YYYY-MM-DD`) |
| `length_series` | number of observations used |
| `min_value`, `max_value` | observed head range [m NAP] |
| `rmse`, `sse`, `mae`, `nse`, `evp`, `rsq`, `kge`, `bic`, `aic`, `aicc` | from `ml.stats.summary()`, pulled by index label out of the `"Value"` column, `None` when absent |
| `response_tmax_95` | `ml.get_response_tmax("Tarso", cutoff=0.95)`, `None` on failure |
| `tmin_cal`, `tmax_cal` | calibration window actually used |
| `resample_rule` | e.g. `"D"` |

Also persist the fitted model itself. Pastas serialises to a **`.pas` file** (JSON) via
`ml.to_file(path)` and reloads with `ps.io.load(path)`. Name it
`{series}_tarso_model.pas`. On Databricks write these to a **Unity Catalog Volume**
(`/Volumes/<catalog>/<schema>/<volume>/models/...`), not DBFS root and not the driver's
local disk; alternatively serialise `ml.to_dict()` to JSON into a Delta column if you
prefer everything in the lakehouse. Keep the statistics rows in a **Delta table** — the
natural replacement for the repo's per-run `model_run_<tag>_tarso.csv`.

Every run must be identifiable: a `run_id` column (the repo uses a `yy_mm_dd_HH_MM`
timestamp) plus the parameters that produced it — forcing file names and columns,
resample rule, validation filter, `min_obs`, calibration window, pastas version. Outputs
are append-only per run, never overwritten in place.

---

## 5. Critical: one failure must not kill the run

In the original, every fit is wrapped in `try`/`except` and the exception message lands
in the report row. Hundreds of series are processed and **individual fits do fail** — too
few observations, degenerate head records, non-converging solvers. The Databricks
implementation must preserve this: no single series may abort the job. If you distribute
with a pandas UDF / `mapInPandas` / `ThreadPoolExecutor`, catch inside the worker
function and return the error as data.

Scaling notes:

- Pastas and the scipy solver underneath it are **single-threaded, CPU-bound, pure
  Python/NumPy**. A pandas UDF over a DataFrame of file paths (one row per series) fits
  well; so does `concurrent.futures` on one large driver if the series count is modest.
- The forcing series are small (a few MB) and needed by every task — **broadcast them**
  rather than re-reading per task.
- If you import `matplotlib` at all, force the `Agg` backend. Per §6 you should not need
  it.
- Pin `pastas==1.12.*` (the original requires Python ≥ 3.12) as a cluster library or via
  `%pip install`. Pastas pulls in numpy, pandas, scipy and matplotlib.

---

## 6. Explicitly out of scope — do NOT port these

The source repo is much larger. Everything below exists there and must be **left out**:

- **All other model types.** The original trains nine: `linear_exp`, `linear_gamma`,
  `linear_dblexp` (linear recharge with Exponential / Gamma / DoubleExponential
  response), `flexmodel_gamma`, `flexmodel_dblexp` (Pastas `FlexModel` recharge), three
  `*_soil` variants adding a root-zone soil-moisture stress, and `tarso`. **Only
  `tarso`.** Keep the model-building call shaped so a second type could be added later,
  but ship one.
- **The extreme-value analysis**, which is the repo's actual research goal: driving the
  calibrated models with a 16-member regional climate ensemble (RACMO / IMPREX,
  1951–2000) to obtain ~704 block extremes per series, fitting GEV/Gumbel tail families,
  computing return levels, and labelling each observed timestep as e.g. a 1-in-30-year
  drought. A large subsystem (extremes runners, tail fits, QQ plots, threshold plots,
  return-period summaries). **None of it.**
- **The Streamlit web interface** (~3000 lines: app, results browser, performance editor,
  indicator viewer). **No dashboard of any kind.**
- **All plotting.** `ml.plots.results()`, `ml.plots.diagnostics()`, per-series figure
  PNGs. Return numbers, not pictures.
- **The Brakenhoff et al. (2022) model checklist** (`ps.check.checklist`) and its
  pass/fail aggregation, the "copy best passing model into `accepted_models/`" step, the
  model-performance summary tables, and the wet/dry indicator step.
- **`OBJECT_DATA.csv` / `timeseries_config.csv` metadata sync.** The repo maintains a
  per-folder CSV describing each borehole (filter top/bottom depth, position in the dike
  cross-section, per-series resample rule, chosen EVT family) and joins it into the
  reports. Interesting later, noise now. If you want the hook, allow an optional
  per-series resample rule; otherwise use the global default.
- **Bias correction and soil-moisture stressor derivation.**
- **KNMI API download notebooks.** Forcing CSVs are handed to you as files.
- Input-validation heuristics the repo carries but does not apply during training
  (physical head ceiling 1.5 m NAP, max step +0.30 / −0.05 m per hour, Tukey IQR outlier
  flagging, dry-period band). Listed only so you recognise them if you meet them; do not
  implement.

---

## 7. Suggested Databricks shape

A deliberately small target, mirroring the above:

1. **Discover** — list head CSVs under `/Volumes/.../input_files/<survey>/`, excluding
   any `OBJECT_DATA.csv`. Produce a DataFrame of `(survey, file_path, series_name)`, one
   row per series.
2. **Load forcings** — read the two forcing CSVs into pandas Series; broadcast.
3. **Train** — distribute step 1's rows. Each task loads its head CSV (§2.1), resamples
   (§3), fits Tarso (§3), writes the `.pas` to the models volume, and returns the stats
   row (§4) — including rows for skips and errors.
4. **Persist** — append the collected rows to a Delta table (e.g. `tarso_runs`), tagged
   with `run_id`.

A single notebook or job with these four tasks is an acceptable and preferred
deliverable. Parameterise: input volume path, survey folder(s), forcing file and column
names, resample rule/method, validation filter, `min_obs`, calibration window, `run_id`,
output volume and table.

**Definition of done:** running the job over one survey folder produces one `.pas` file
per successfully trained series and one Delta row per series (successful, skipped or
errored), with fit statistics that look sane — `rsq`/`nse` typically 0.7–0.95 for a
healthy series.
