# Committed forcing data

`knmi_forcing_<station>.csv` — daily precipitation and Makkink evaporation in mm,
one row per day, produced by:

```bash
python scripts/check_knmi.py          # refreshes the file for the default station
```

These files are **committed on purpose**. They are small (~58 KB for 2020-to-date)
and the Databricks cluster has no outbound internet, so shipping them with the code
means a Git folder pull is the whole deployment — no upload step, and the data
version is pinned to the commit that used it.

Read them with the normal loader; the column picks the variable:

```python
from tarso_pipeline.io import load_forcing
from tarso_pipeline.paths import forcing_path

path = forcing_path(249)
precipitation = load_forcing(path, "precipitation_mm")   # pd.Series, mm/day
evaporation   = load_forcing(path, "makkink_mm")         # pd.Series, mm/day
```

Refreshing is a commit, so keep those commits separate from code changes.

## Radar forcing and extra stresses (since 2026-11-24)

These files sit next to the station file and are read by `tarso_pipeline.inputs` for the extended model types:

| file | columns | what |
|---|---|---|
| `radar_forcing_<polder>.csv` (beemster, purmer, heerhugowaard) | date, precipitation_mm, makkink_mm, precipitation_source | KNMI gauge-adjusted daily radar (`rad_nl25_rac_mfbs_24h`) at one pixel per polder, the 08–08 UTC sum moved one day back. Outside the radar record (from 2019-12-31; the product lags about a month) the station's precipitation, marked per day. Evaporation is Berkhout Makkink. |
| `stress_lake_edam.csv` | date, value | RWS Markermeer level at Edam, daily mean, m NAP |
| `stress_pressure_240.csv` | date, value | KNMI 240 Schiphol daily mean sea-level pressure, hPa |

```bash
python scripts/build_forcing_sources.py            # rebuild from the local caches
python scripts/build_forcing_sources.py --fetch    # refresh KNMI, RWS and the radar first (internet)
```

On Databricks, the fit and extremes notebooks look in the `forcing_dir` volume first and fall back to this folder, so a Git folder pull is enough. Upload the files to the volume only if you want the volume to win.
