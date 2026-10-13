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
