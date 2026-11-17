# Monitoring: daily tables and the `Grondwater_Monitoring` dashboard

| what | where |
|---|---|
| daily job (writes `monitoring_*` tables) | `notebooks/build_monitoring_tables.py` |
| SQL of those tables | `src/monitoring_lib/sql.py` |
| flag counts (the review app's own checks) | `src/monitoring_lib/flags.py` -> `data_validation/local_validation/checks.py` |
| dashboard definition (edit this) | `src/monitoring_lib/dashboard.py` |
| dashboard file (generated) | `../databricks_dashboard/Grondwater_Monitoring.lvdash.json` |

## Pages
1. **Data-inventarisatie & datastroom**
   - series, measurements and the cs_objecten match per supplier;
   - active/inactive, missing metadata;
   - a status map, and the sensors without data for 24 h / 7 days;
   - uptime per supplier against the contract target, per sensor, and a 14-day delivery heatmap.
2. **Meetinventarisatie & datakwaliteit**
   - share validated, and the flags per series (v1–v4, rules A/B);
   - suspect data that is not yet approved;
   - yesterday's data, the big swings of the last week with KNMI rainfall, wetter or drier than normal.
3. **Reeks-specifiek**
   - pick a series to see its sensor data, validated data and hand readings on the same well;
   - the Pastas simulation, the filter bottom and the top of the well;
   - the last 30 days hourly, and its flags.
4. **Pastas**
   - two maps, unvalidated (R²) and validated (colours as below);
   - every model type per validated series with its checks.

Validated map:
- **grijs:** no validated data or model.
- **rood:** not accepted (fit failed, runaway in the extremes, not eligible).
- **geel:** has an R² but fails R² ≥ 0.7, t95 or the parameter bounds.
- **groen:** passes all three.

## First time on Databricks
1. **Pull:** pull `development` in the Git folder.
2. **Build the tables:** open `monitoring/notebooks/build_monitoring_tables.py` and *Run all*.
   - Defaults write to `waterkeringbeheer_prd.jvanruitenbeek`.
   - The first run computes 90 days of uptime.
   - For more history, set `uptime_days` higher once, e.g. 365.
3. **Open the dashboard:** open `databricks_dashboard/Grondwater_Monitoring.lvdash.json`, choose a SQL warehouse, check the pages and **Publish**.
4. **Create the job:** Workflows → *Create job*.
   - Task type: Notebook, source: the Git folder (branch `development`, or `main` after the merge).
   - Path: `monitoring/notebooks/build_monitoring_tables`.
   - Schedule: daily at 06:00 Europe/Amsterdam.
   - Compute: the same cluster or serverless as the other notebooks.
   - The widgets keep their defaults; `as_of` blank = today.
5. **Refresh the dashboard daily (optional):** in the dashboard, *Schedule* → daily at 06:30.

## Changing the dashboard
Edit `src/monitoring_lib/dashboard.py`, then run:

    python monitoring/scripts/build_dashboard.py
    python -m pytest monitoring -q

The script refuses to write when a widget points at a missing dataset or column.

Edits made in the Databricks UI are saved into the `.lvdash.json` and are overwritten the next time the script runs. Copy them into `dashboard.py` first, or ask Claude to.

## Settings
- **Contract target:** `UPTIME_DOEL_PCT` in `dashboard.py`, default 95 %.
- **Silent:** a sensor is silent after 24 h without data (`SILENT_HOURS` in `sql.py`).
- **Suspect swing:** a range over 0.5 m in a week with less than 10 mm of rain. Both are job widgets, `swing_m` and `little_rain_mm`.
- **Flag thresholds:** `Thresholds` in `data_validation/local_validation/checks.py`, the same as the review app's.
- **Suspect:** v1–v4 or rule B. Rule A is shown, but is report-only, as in the review app.
- **Validated:** an hour is validated when it is in `validated_meting`, i.e. approved. Rejected hours count as not validated.
