"""Local forcing and extra stresses: fetch once, cache as CSV, load per series.

Sources (all public, all published for the past and ongoing):

KNMI neerslagstations  daily ``RD`` (mm), the voluntary network, 08-08 UT, labelled by the
                       day it ends. ``monv/reeksen`` on daggegevens.knmi.nl.
KNMI automatic         daily ``EV24`` Makkink (Berkhout 249, Schiphol 240) and ``PG`` mean
                       sea-level pressure (Schiphol 240; Berkhout has none). ``daggegevens``.
RWS                    Markermeer water level ``WATHTE`` (cm NAP, 10-minute), daily means.
                       ddapi20-waterwebservices.rijkswaterstaat.nl.
HHNK                   outer-water series from the earlier pastas_machine project
                       (Drieban 2019-2025, Oosterpolder Hoorn 2023-2025): not ongoing.

Caches: ``pastas/data/forcing/local/`` and ``pastas/data/stresses/``.
"""

from __future__ import annotations

import io
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

PASTAS = Path(__file__).resolve().parents[2]
REPO = PASTAS.parent
LOCAL = PASTAS / "data" / "forcing" / "local"
STRESSES = PASTAS / "data" / "stresses"
OBJECT_DATA = REPO / "data_validation" / "input_output" / "output_databricks" / "metadata" / "object_data.csv"
HHNK_DIR = REPO.parent / "pastas_machine" / "input_files" / "stressors"

KNMI_RD_URL = "https://www.daggegevens.knmi.nl/klimatologie/monv/reeksen"
KNMI_DAY_URL = "https://www.daggegevens.knmi.nl/klimatologie/daggegevens"
RWS_URL = "https://ddapi20-waterwebservices.rijkswaterstaat.nl/ONLINEWAARNEMINGENSERVICES/OphalenWaarnemingen"

# The neerslagstations around the series that run from 1999 to now (checked 2026-11-19).
# KNMI's RD list gives no coordinates: these are the village centres, good to ~1-2 km,
# which hardly moves an inverse-distance weight over stations 5-15 km apart.
RD_STATIONS = {
    222: ("Hoorn", 52.642, 5.060),
    224: ("Edam", 52.513, 5.048),
    233: ("Zaandam (Hembrug)", 52.418, 4.832),
    242: ("Purmerend", 52.503, 4.957),
    251: ("West Beemster", 52.548, 4.878),
    256: ("Marken", 52.457, 5.103),
}
# From the daggegevens header (exact).
EV_STATIONS = {249: ("Berkhout", 52.644, 4.979), 240: ("Schiphol", 52.318, 4.790)}
PRESSURE_STATION = 240
RWS_LOCATIONS = {"edam": (52.516, 5.070), "markermeer.midden": (52.532, 5.231)}
HHNK_FILES = {"drieban": "Buitenwaterstand_Drieban_clean.csv", "hoorn": "Buitenwaterstand_Oosterpolder_Hoorn.csv"}


# --- geometry -----------------------------------------------------------------------

def distance_km(lat1, lon1, lat2, lon2) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi, dlmb = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 6371.0 * 2 * math.asin(math.sqrt(a))


def idw_weights(target: tuple[float, float], stations: dict, n: int = 3, power: float = 2.0) -> dict:
    """Inverse-distance weights of the ``n`` nearest stations; they sum to 1."""
    dist = {k: max(distance_km(*target, v[-2], v[-1]), 0.1) for k, v in stations.items()}
    nearest = sorted(dist, key=dist.get)[:n]
    raw = {k: dist[k] ** -power for k in nearest}
    total = sum(raw.values())
    return {k: w / total for k, w in raw.items()}


def series_locations() -> dict[str, tuple[float, float]]:
    """``meetreeks_id -> (lat, lon)`` for the series that have coordinates."""
    frame = pd.read_csv(OBJECT_DATA, usecols=["meetreeks_id", "lat", "lon"]).dropna()
    return {r.meetreeks_id: (float(r.lat), float(r.lon)) for r in frame.itertuples()}


# --- KNMI ---------------------------------------------------------------------------

def _knmi_frame(text: str, names: list[str]) -> pd.DataFrame:
    lines = [l for l in text.splitlines() if l.strip() and not l.startswith("#")]
    frame = pd.read_csv(io.StringIO("\n".join(lines)), header=None, skipinitialspace=True, names=names)
    frame["date"] = pd.to_datetime(frame["date"].astype(str), format="%Y%m%d")
    return frame


def fetch_knmi(start: str = "19990101", end: str | None = None) -> None:
    """Download RD for RD_STATIONS and EV24/PG for EV_STATIONS into the cache."""
    import requests

    end = end or pd.Timestamp.today().strftime("%Y%m%d")
    LOCAL.mkdir(parents=True, exist_ok=True)
    rd = requests.post(KNMI_RD_URL, data={"start": start, "end": end, "vars": "RD",
                                          "stns": ":".join(map(str, RD_STATIONS))}, timeout=600)
    rd.raise_for_status()
    frame = _knmi_frame(rd.text, ["stn", "date", "RD", "SX"])
    frame["RD"] = pd.to_numeric(frame["RD"], errors="coerce") / 10.0  # 0.1 mm -> mm
    frame.loc[frame["RD"] < 0, "RD"] = 0.0  # -1: < 0.05 mm
    frame[["stn", "date", "RD"]].to_csv(LOCAL / "knmi_rd_stations.csv", index=False)

    stations = sorted(set(EV_STATIONS) | {PRESSURE_STATION})
    day = requests.post(KNMI_DAY_URL, data={"start": start, "end": end, "vars": "EV24:PG",
                                            "stns": ":".join(map(str, stations))}, timeout=600)
    day.raise_for_status()
    frame = _knmi_frame(day.text, ["stn", "date", "EV24", "PG"])
    frame["EV24"] = pd.to_numeric(frame["EV24"], errors="coerce") / 10.0  # 0.1 mm -> mm
    frame["PG"] = pd.to_numeric(frame["PG"], errors="coerce") / 10.0      # 0.1 hPa -> hPa
    frame.to_csv(LOCAL / "knmi_auto_stations.csv", index=False)


def _pivot(path: Path, column: str) -> pd.DataFrame:
    frame = pd.read_csv(path, parse_dates=["date"])
    return frame.pivot_table(index="date", columns="stn", values=column).sort_index()


def weighted(table: pd.DataFrame, weights: dict) -> pd.Series:
    """Weighted mean per day; a station missing that day drops out and the rest renormalise."""
    cols = [k for k in weights if k in table.columns]
    values = table[cols]
    w = pd.DataFrame({k: weights[k] for k in cols}, index=values.index).where(values.notna())
    return (values * w).sum(axis=1) / w.sum(axis=1)


def local_precipitation(target, n: int = 3, shift_days: int = -1) -> pd.Series:
    """IDW precipitation (mm/day) at ``target``; ``shift_days=-1`` moves 08-08 sums to the day they mostly fall in."""
    table = _pivot(LOCAL / "knmi_rd_stations.csv", "RD")
    series = weighted(table, idw_weights(target, RD_STATIONS, n=n)).dropna()
    series.index = series.index + pd.Timedelta(days=shift_days)
    return series.rename("precipitation_mm")


def local_evaporation(target) -> pd.Series:
    table = _pivot(LOCAL / "knmi_auto_stations.csv", "EV24")
    return weighted(table, idw_weights(target, EV_STATIONS, n=2)).dropna().rename("makkink_mm")


def pressure() -> pd.Series:
    table = _pivot(LOCAL / "knmi_auto_stations.csv", "PG")
    return table[PRESSURE_STATION].dropna().rename("pressure_hpa")


# --- water levels -------------------------------------------------------------------

def fetch_rws(code: str, start: str = "2015-01-01", end: str | None = None) -> Path:
    """Daily mean WATHTE (m NAP) of one RWS location, fetched month by month."""
    import requests

    end_ts = pd.Timestamp(end) if end else pd.Timestamp.today().normalize()
    months = pd.date_range(pd.Timestamp(start), end_ts, freq="MS")
    daily = []
    for first in months:
        last = min(first + pd.offsets.MonthBegin(1), end_ts + pd.Timedelta(days=1))
        body = {"Locatie": {"Code": code},
                "AquoPlusWaarnemingMetadata": {"AquoMetadata": {"Compartiment": {"Code": "OW"},
                                                                "Grootheid": {"Code": "WATHTE"}}},
                "Periode": {"Begindatumtijd": first.strftime("%Y-%m-%dT00:00:00.000+01:00"),
                            "Einddatumtijd": last.strftime("%Y-%m-%dT00:00:00.000+01:00")}}
        reply = requests.post(RWS_URL, json=body, timeout=300)
        if reply.status_code != 200:
            continue  # a month without data answers 204/400; leave it as a gap
        values = []
        for block in json.loads(reply.text).get("WaarnemingenLijst", []):
            if (block["AquoMetadata"].get("ProcesType") or "meting") != "meting":
                continue  # skip forecasts ("verwachting")
            for m in block["MetingenLijst"]:
                v = m["Meetwaarde"].get("Waarde_Numeriek")
                if v is not None and abs(v) < 1000:  # 999999999 = no value
                    values.append((m["Tijdstip"], v / 100.0))
        if values:
            frame = pd.DataFrame(values, columns=["time", "level"])
            frame["time"] = pd.to_datetime(frame["time"], utc=True).dt.tz_convert("Europe/Amsterdam").dt.tz_localize(None)
            daily.append(frame.set_index("time")["level"].resample("D").mean())
        print(f"  rws {code} {first:%Y-%m}: {len(values)} values", flush=True)
    STRESSES.mkdir(parents=True, exist_ok=True)
    path = STRESSES / f"rws_{code}_daily.csv"
    out = pd.concat(daily).groupby(level=0).mean().dropna()
    out.rename_axis("date").rename("level_m_nap").to_csv(path)
    return path


def rws_level(code: str = "edam") -> pd.Series:
    frame = pd.read_csv(STRESSES / f"rws_{code}_daily.csv", parse_dates=["date"], index_col="date")
    level = frame["level_m_nap"]
    # The Markermeer is managed between about -0.5 and +0.5 m NAP (record Jan 2024: +0.43
    # at Edam); a daily mean outside +-1 m is a sensor error (midden has a 2.28 m spike).
    return regular(level[level.abs() <= 1.0]).rename(f"rws_{code}")


def hhnk_level(name: str) -> pd.Series:
    """Daily mean of one HHNK outer-water file (m NAP)."""
    path = HHNK_DIR / HHNK_FILES[name]
    frame = pd.read_csv(path)
    time, value = frame.columns[:2]
    stamps = pd.to_datetime(frame[time], format="mixed", errors="coerce")
    series = pd.Series(pd.to_numeric(frame[value], errors="coerce").to_numpy(), index=stamps).dropna()
    series = series[series.index.notna()].sort_index()
    return regular(series.resample("D").mean()).rename(f"hhnk_{name}")


def regular(series: pd.Series, limit: int = 7) -> pd.Series:
    """On every day (Pastas needs a regular index); gaps up to ``limit`` days interpolated,
    longer ones left NaN for Pastas' own ``fill_nan``."""
    series = series.dropna()
    return series.asfreq("D").interpolate(limit=limit, limit_area="inside")


def anomaly(series: pd.Series, around: pd.Series | None = None) -> pd.Series:
    """The stress minus its mean over the calibration days (or over the whole series)."""
    base = series if around is None else series.loc[around.index.min():around.index.max()]
    return series - float(base.mean())
