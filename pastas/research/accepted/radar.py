"""KNMI gauge-adjusted daily radar precipitation at one point per polder.

    python research/accepted/radar.py 2020 2026        # download, extract, cache, delete the zips

Dataset ``rad_nl25_rac_mfbs_24h`` version 2.0 on the KNMI Data Platform: the climatological
radar product, 24-hour sums on a 1 km grid, adjusted with *both* KNMI gauge networks
(automatic + the daily neerslagstations), HDF5, CC BY 4.0. It is published as one zip per
year (600-900 MB) holding a rolling 24-hour sum every hour; the file ending at 08:00 UTC is
the 08-08 UTC day of the neerslagstations (``RD``), labelled by the day it ends.

Access: the Open Data API needs a key in the ``Authorization`` header. ``KNMI_API_KEY`` is
used if set; otherwise the *anonymous* key KNMI publishes for everyone on
https://developer.dataplatform.knmi.nl/open-data-api (shared, rate-limited, valid until
2027-08-01; replace it from that page when it expires). The yearly zips need only two calls
each, so the anonymous key is enough.

Points (``stresses.polder_points``): the centroid of each polder's piezometers. One pixel per
polder, the nearest grid cell; no averaging.

Cache: ``pastas/data/forcing/local/radar_<polder>.csv`` (``date, precipitation_mm``), the
date being the end of the 08-08 UTC window, like ``knmi_rd_stations.csv``.
"""

from __future__ import annotations

import io
import os
import sys
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd

PASTAS = Path(__file__).resolve().parents[2]
for path in (PASTAS, PASTAS / "src"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from research.accepted import stresses  # noqa: E402

DATASET = "rad_nl25_rac_mfbs_24h"
VERSION = "2.0"
API = f"https://api.dataplatform.knmi.nl/open-data/v1/datasets/{DATASET}/versions/{VERSION}/files"
# KNMI's public anonymous key (see the module docstring); not a secret.
ANONYMOUS_KEY = ("eyJvcmciOiI1ZTU1NGUxOTI3NGE5NjAwMDEyYTNlYjEiLCJpZCI6IjUzYTg1ZDBhMmQ5YzRkYzJiYWNlNzQ4NTQ2Zjk4ODEx"
                 "IiwiaCI6Im11cm11cjEyOCJ9")
CACHE = stresses.LOCAL
ZIPS = PASTAS / "local_runs" / "research" / "radar" / "zips"
POLDERS = ("Beemster", "Purmer", "Heerhugowaard")


def zip_name(year: int) -> str:
    return f"RADNL_CLIM____MFBSNL25_24H_{year - 1}1231T010000_{year + 1}0101T000000_0002.zip"


def download(year: int, folder: Path = ZIPS) -> Path:
    """Download one yearly zip (skipped if already there), retrying while rate-limited."""
    import time

    import requests

    folder.mkdir(parents=True, exist_ok=True)
    path = folder / zip_name(year)
    if path.exists() and (folder / f"{path.name}.done").exists():
        return path
    key = os.environ.get("KNMI_API_KEY", ANONYMOUS_KEY)
    for attempt in range(40):
        reply = requests.get(f"{API}/{path.name}/url", headers={"Authorization": key}, timeout=60)
        if reply.status_code == 200:
            break
        time.sleep(60)  # the anonymous key is shared: wait out the rate limit
    else:
        raise RuntimeError(f"no download url for {path.name}: {reply.status_code} {reply.text[:200]}")
    with requests.get(reply.json()["temporaryDownloadUrl"], stream=True, timeout=3600) as stream:
        stream.raise_for_status()
        with open(path, "wb") as out:
            for chunk in stream.iter_content(chunk_size=1 << 22):
                out.write(chunk)
    (folder / f"{path.name}.done").touch()
    return path


def grid_index(lat: float, lon: float, geographic: dict) -> tuple[int, int]:
    """``(row, column)`` of the grid cell holding ``(lat, lon)``.

    The grid is polar stereographic (proj4 string in the file); projected km ``x, y`` map to
    ``column = x / size_x - column_offset`` and ``row = y / size_y - row_offset``.
    """
    from pyproj import Proj

    proj = Proj(geographic["proj4"])
    x, y = proj(lon, lat)  # km: the proj4 ellipsoid is given in km (+a=6378.137)
    column = x / geographic["size_x"] - geographic["column_offset"]
    row = y / geographic["size_y"] - geographic["row_offset"]
    return int(np.floor(row)), int(np.floor(column))


def _geographic(h5) -> dict:
    g = h5["geographic"].attrs
    return {"proj4": h5["geographic/map_projection"].attrs["projection_proj4_params"].decode(),
            "size_x": float(g["geo_pixel_size_x"][0]), "size_y": float(g["geo_pixel_size_y"][0]),
            "column_offset": float(g["geo_column_offset"][0]), "row_offset": float(g["geo_row_offset"][0]),
            "rows": int(g["geo_number_rows"][0]), "columns": int(g["geo_number_columns"][0]),
            "corners": g["geo_product_corners"].reshape(4, 2)}


def extract(path: Path, points: dict[str, tuple[float, float]]) -> pd.DataFrame:
    """Daily (08 UTC) values at ``points`` from one yearly zip: ``date x polder`` in mm."""
    import h5py

    rows, cells = [], None
    with zipfile.ZipFile(path) as archive:
        names = sorted(n for n in archive.namelist() if n.endswith("0800_NL.h5"))
        for name in names:
            with h5py.File(io.BytesIO(archive.read(name)), "r") as h5:
                if cells is None:
                    geographic = _geographic(h5)
                    cells = {p: grid_index(*where, geographic) for p, where in points.items()}
                image = h5["image1/image_data"]
                values = {}
                for polder, (r, c) in cells.items():
                    raw = int(image[r, c])
                    values[polder] = np.nan if raw == 65535 else 0.01 * raw  # GEO = 0.01 * PV (mm)
            stamp = pd.Timestamp(name.rsplit("_", 2)[-2][:8])
            rows.append({"date": stamp, **values})
    return pd.DataFrame(rows).set_index("date").sort_index()


def check_grid(path: Path) -> pd.DataFrame:
    """The four product corners (lon, lat) mapped back to grid indices: should be the image corners."""
    import h5py

    with zipfile.ZipFile(path) as archive:
        name = next(n for n in archive.namelist() if n.endswith("0800_NL.h5"))
        with h5py.File(io.BytesIO(archive.read(name)), "r") as h5:
            geographic = _geographic(h5)
    out = []
    for lon, lat in geographic["corners"]:
        r, c = grid_index(float(lat), float(lon), geographic)
        out.append({"lon": float(lon), "lat": float(lat), "row": r, "column": c})
    return pd.DataFrame(out)


def update(years: list[int], keep_zips: bool = False) -> pd.DataFrame:
    """Download, extract and cache the polder points for ``years``; returns the full table."""
    points = {p: stresses.polder_points()[p] for p in POLDERS}
    frames = []
    for year in years:
        path = download(year)
        frames.append(extract(path, points))
        print(f"  radar {year}: {len(frames[-1])} days", flush=True)
        if not keep_zips:
            path.unlink()
    new = pd.concat(frames)
    CACHE.mkdir(parents=True, exist_ok=True)
    for polder in POLDERS:
        file = CACHE / f"radar_{polder.lower()}.csv"
        old = pd.read_csv(file, parse_dates=["date"], index_col="date")["precipitation_mm"] if file.exists() else None
        series = new[polder].rename("precipitation_mm")
        if old is not None:
            series = pd.concat([old[~old.index.isin(series.index)], series]).sort_index()
        series.rename_axis("date").round(2).to_frame().to_csv(file)
    return new


def load(polder: str) -> pd.Series:
    file = CACHE / f"radar_{polder.lower()}.csv"
    return pd.read_csv(file, parse_dates=["date"], index_col="date")["precipitation_mm"].rename(f"radar_{polder.lower()}")


if __name__ == "__main__":
    first, last = int(sys.argv[1]), int(sys.argv[2])
    update(list(range(first, last + 1)))
