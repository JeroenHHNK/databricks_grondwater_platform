# Databricks dashboards

The AI/BI (Lakeview) dashboards of the groundwater platform. They are kept in git as `.lvdash.json` files. Databricks opens them from the Git folder, and a save in the Databricks UI writes back into the same file.

| file | status | what it monitors | reads |
|---|---|---|---|
| `Grondwater_Monitoring.lvdash.json` | **current**, generated | data flow, data quality, single series, Pastas models | the `monitoring_*` tables (daily job), plus `validated_meting` and `valid_pastas_simulations` |
| `MOAD.lvdash.json` | older, hand-made | inventory in `hl_cs_objecten`, Fugro/Wiertsema API delivery and quality, maps | `gold_prd` live (`meting`, `meetreeks`, `object`, `meetlocatie`) |
| `Pastas_Dashboard.lvdash.json` | older, hand-made | map of the first unvalidated Tarso fits | `pastas_fit_metrics` / `pastas_model_checks`: the **old table names**, from before the `unvalid_pastas_` / `valid_pastas_` split |

`Grondwater_Monitoring` replaces the other two:
- **Live scans:** MOAD's pages scan `meting` live on every refresh. The monitoring dashboard reads small tables that a job builds once a day.
- **Pastas tables:** the Pastas dashboard reads tables under names the pipeline no longer writes.

The older two are kept until the new one has been checked on Databricks.

All tables live in `waterkeringbeheer_prd.jvanruitenbeek`. The source `gold_prd` is read-only and is never written.

## Grondwater_Monitoring

**Do not edit the JSON by hand.** It is generated from `monitoring/src/monitoring_lib/dashboard.py` by `python monitoring/scripts/build_dashboard.py`.

A layout change made in the Databricks UI is saved into the JSON. The next run of the script overwrites it, so copy the change into `dashboard.py` first.

The daily job, its settings and the first-time setup are in [`monitoring/README.md`](../monitoring/README.md).

### Where the data comes from

```
gold_prd.hl_grondwaterpeilmeting.meting / meetreeks / object ─┐
gold_prd.hl_cs_objecten.meetlocatie (positions, cs match) ────┤
validated_meting (data_validation 03, approved hours) ────────┤  monitoring/notebooks/
<prefix>_fit_metrics / model_checks / model_selection /       ├─ build_monitoring_tables.py ──► monitoring_* ──► dashboard
  model_in_use (Pastas, both data bases) ─────────────────────┤  (daily, 06:00)
KNMI forcing CSV on the volume (station 249 Berkhout) ────────┘
```

| table | one row per | holds |
|---|---|---|
| `monitoring_hourly` | series × UTC hour | the mean raw value of that hour and how many raw rows it had (the series page plots this) |
| `monitoring_series_status` | series | supplier, cs_objecten match and key, active/inactive, which metadata fields are filled, first/last value, hours in the last 7 days, status (`ok` / `stil > 24 uur` / `stil > 7 dagen` / `nooit data`), share of hours validated, whether a Pastas model exists |
| `monitoring_uptime_daily` | sensor × day | hours received out of 24; the history is kept, the last 90 days are recomputed |
| `monitoring_flags_series` | series | the review app's flags (v1–v4, rules A/B) over the whole series, the last 7 days and yesterday; suspect hours not yet validated |
| `monitoring_change_recent` | series | yesterday's mean vs the 14-day baseline, the range over the last week, rainfall over that week, wet/dry index |
| `monitoring_knmi_daily` | day | KNMI precipitation and Makkink evaporation |
| `monitoring_pastas_models` | series × model type × data basis | fit (R², holdout R², RMSE, AIC, t95), the checks, the selection, whether it is in use |
| `monitoring_pastas_series` | series | the model in use on each basis and the map colour |

### The four pages

**1. Data-inventarisatie & datastroom: is the data arriving?**
- **Inventory:**
  - totals per supplier (Fugro, Wiertsema);
  - how many series are linked to `hl_cs_objecten` and by which key;
  - active vs inactive;
  - which metadata is missing (filter depths, top of well, location code, coordinates).
- **Map:** every sensor, coloured by status.
- **Silent sensors:** a table of sensors without data for more than 24 hours or 7 days.
- **Uptime:**
  - % of hours received per supplier per day, against the contract target (`UPTIME_DOEL_PCT`, 95 %);
  - per sensor over 7, 30 and 90 days, lowest first;
  - a 14-day delivery heatmap.
- Hand readings (`HANDWAARNEMING`) are not expected hourly and are left out of uptime and flags.

**2. Meetinventarisatie & datakwaliteit: is the data any good?**
- **Validated share per series:** hours in `validated_meting` ÷ hours measured.
- **Flags per series, top 30:** with the same checks and thresholds as the review app (`data_validation/local_validation/checks.py`).
  - *Verdacht* (suspect) = v1, v2, v3, v4 or rule B.
  - Rule A is shown, but it is report-only, as in the review app.
- **Suspect data not yet validated:** the review work list.
- **Yesterday:** values received, flags and the change from the baseline.
- **Big swings over the last week, with KNMI rainfall:** a range over 0.5 m with less than 10 mm of rain is marked suspect, because rain does not explain it.
- **Wetter or drier than normal:** yesterday against the series' own median and extremes, from −100 to +100.

**3. Reeks-specifiek: one series in full.**
- **Pick a series:** a dropdown on the location code and series. The queries filter on it in SQL.
- **Headline facts:**
  - supplier and status;
  - validated yes/no;
  - Pastas model and its type;
  - whether the metadata is complete.
- **Daily head chart**, with:
  - the raw sensor data;
  - the validated data;
  - hand readings on the same well;
  - the Pastas simulation (validated model in use);
  - the filter bottom and the top of the well as reference lines.
- **Hourly detail:** the last 30 days hourly and the series' flag counts.

**4. Pastas: are the models usable?**
- **Unvalidated map:** the model in use on the raw data, coloured by R² (≥ 0.7 or not).
- **Validated map:** the model in use on the validated data, coloured by the rule below.
  - **Hover:** validated and unvalidated R², model type, response time t95, RMSE, holdout R², who picked the model, and how many of the model types pass.
- **All validated models:** a table with every model type of every validated series and its checks (`pass_rsq`, `pass_memory`, `pass_bounds`, `pass_gain`), tier, reason and whether it was selected.

| colour | meaning | what to do |
|---|---|---|
| **grijs** (gray) | no validated data or no validated model | review the series first (data validation), then fit with `data_basis=validated` |
| **rood** (red) | validated, but not accepted: the fit failed, the model ran away in the extremes (T=100 more than 1.5 m outside the measured range), or no model type is eligible | look at the data and the fit; this series has no usable model |
| **geel** (yellow) | has an R², but fails at least one of R² ≥ 0.7, response memory (t95 < half the calibration period) or parameter bounds | **review**: check the fit in the app (*Pastas → Modellen*), pick another model type or accept it knowingly |
| **groen** (green) | the model in use passes all three checks | usable |

The map's green uses three checks: R², memory and bounds. The pipeline's automatic pick uses a stricter "passed" stamp that also requires the gain check (`pastas/README.md`). A series can therefore be green on the map while its pick says `best_effort`. The *pass_gain* column in the table shows this.

### Refresh

1. The job `build_monitoring_tables` runs daily at 06:00 and rebuilds the tables.
2. Optionally, set the dashboard's own *Schedule* to refresh its cache at 06:30, so it opens on the morning's tables.
3. Pastas results change only when the Pastas notebooks have run. Model picks made in the app change only after `load_family_choice`.

## MOAD (older)

Hand-built in the Databricks UI. Its pages:
- **`Inventarisatie_CSO_API`:** wells in `hl_cs_objecten` after 2022, supplier split, completeness of the fields, matched vs unmatched to `meetreeks`.
- **`Fugro_API` / `Wiertsema_API`:** delivery heatmaps per day, and Wiertsema data quality over the full series and 14 days. It also has trend (`Trend14d`) and wet/dry (`Wet_Dry`).
- **`Kaartje`:** maps.

Everything is computed live from `gold_prd`. The monitoring dashboard took its ideas (trend vs 14-day baseline, wet/dry index, delivery heatmap) and computes them in the daily job instead.

## Pastas_Dashboard (older)

One map page of the first Tarso fits, joined to `meetlocatie` for positions.

It reads `pastas_fit_metrics` and `pastas_model_checks`. Those tables were renamed to `unvalid_pastas_*` (see `pastas/README.md`), so the dashboard shows nothing once the rename is done.

Its `pastas_fit_map_2` dataset also pivots the checks under labels the pipeline never writes, so its memory and bounds columns are always empty. The Pastas page of `Grondwater_Monitoring` replaces it.

## Adding or changing a dashboard

- **New dashboard:**
  1. Create it in Databricks inside this folder of the Git folder (*Create → Dashboard*).
  2. Commit and push from the Git folder.
  3. Pull locally.
- **File names:** avoid colons and other characters Windows cannot check out. A name like `x 2026-11-17 13:53:15.lvdash.json` blocks every `git pull` on Windows.
- **Grondwater_Monitoring:** change `dashboard.py`, run the build script and the tests (`python -m pytest monitoring -q`). The script refuses to write when a widget points at a missing dataset or column.
