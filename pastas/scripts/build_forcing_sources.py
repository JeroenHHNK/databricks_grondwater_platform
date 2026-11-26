"""Write the radar forcing and the extra stresses next to the KNMI forcing in ``data/forcing/``.

    python scripts/build_forcing_sources.py            # from the local caches
    python scripts/build_forcing_sources.py --fetch    # refresh the caches first (internet)

Writes, in the format ``tarso_pipeline.inputs`` reads (and the same folder as
``knmi_forcing_<station>.csv``, so a Git folder pull or an upload of that folder to the
volume's ``knmi_forcing`` carries everything):

``radar_forcing_<polder>.csv``  date, precipitation_mm, makkink_mm, precipitation_source
    KNMI gauge-adjusted daily radar at the polder's point (``research/accepted/radar.py``,
    one pixel at the centroid of the polder's piezometers), the 08-08 UTC sum moved one
    day back. Before and after the radar record the station's precipitation, marked per
    day (``knmi_<station>``); evaporation is always the station's Makkink.
``stress_lake_edam.csv``        date, value: RWS Markermeer at Edam, daily mean, m NAP
    (outside +-1 m dropped as sensor error; gaps up to 7 days interpolated).
``stress_pressure_240.csv``     date, value: KNMI 240 Schiphol daily mean sea-level
    pressure (PG), hPa. Berkhout has no pressure.

``--fetch`` refreshes the caches it builds from: the KNMI daggegevens (PG), RWS Edam, and
the radar of the current and previous year (the radar product lags about a month). The
Databricks cluster has no internet, so this runs locally and the files are committed.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

PASTAS = Path(__file__).resolve().parents[1]
for path in (PASTAS, PASTAS / "src"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from tarso_pipeline.forcing import forcing_filename  # noqa: E402
from tarso_pipeline.inputs import RADAR_POLDERS, STRESS_FILES, radar_filename  # noqa: E402

FORCING = PASTAS / "data" / "forcing"


def radar_forcing(polder: str, station: str = "249") -> pd.DataFrame:
    from research.accepted import radar

    knmi = pd.read_csv(FORCING / forcing_filename(station), parse_dates=["date"]).set_index("date").sort_index()
    pixel = radar.load(polder.capitalize()).dropna()
    pixel.index = pixel.index - pd.Timedelta(days=1)  # 08-08 UTC, labelled by its end -> the day it mostly falls in
    out = knmi[["precipitation_mm", "makkink_mm"]].copy()
    out["precipitation_source"] = f"knmi_{station}"
    days = out.index.intersection(pixel.index)
    out.loc[days, "precipitation_mm"] = pixel.loc[days].to_numpy()
    out.loc[days, "precipitation_source"] = f"radar_{polder}"
    return out.rename_axis("date").reset_index()


def lake_edam() -> pd.DataFrame:
    from research.accepted import stresses

    level = stresses.rws_level("edam").dropna()  # daily, |level| <= 1 m, gaps <= 7 days interpolated
    return pd.DataFrame({"date": level.index, "value": level.round(4).to_numpy()})


def pressure_240() -> pd.DataFrame:
    from research.accepted import stresses

    pg = stresses.pressure()
    return pd.DataFrame({"date": pg.index, "value": pg.round(1).to_numpy()})


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--fetch", action="store_true", help="refresh the caches first (needs internet)")
    parser.add_argument("--station", default="249")
    args = parser.parse_args()
    if args.fetch:
        from research.accepted import radar, stresses

        stresses.fetch_knmi()
        stresses.fetch_rws("edam")
        year = pd.Timestamp.today().year
        radar.update([year - 1, year])
    for polder in RADAR_POLDERS:
        frame = radar_forcing(polder, args.station)
        frame.to_csv(FORCING / radar_filename(polder), index=False, date_format="%Y-%m-%d")
        radar_days = frame["precipitation_source"].str.startswith("radar")
        print(f"{radar_filename(polder)}: {len(frame)} days, radar {frame.loc[radar_days, 'date'].min().date()} .. "
              f"{frame.loc[radar_days, 'date'].max().date()}")
    for name, frame in (("lake", lake_edam()), ("pressure", pressure_240())):
        frame.to_csv(FORCING / STRESS_FILES[name], index=False, date_format="%Y-%m-%d")
        print(f"{STRESS_FILES[name]}: {len(frame)} days, {frame['date'].min().date()} .. {frame['date'].max().date()}")


if __name__ == "__main__":
    main()
