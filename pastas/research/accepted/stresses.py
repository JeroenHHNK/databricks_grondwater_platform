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


BOEZEM_FILES = {
    # Schermerboezem gauges (HHNK, 15-minute, m NAP). Same water body (r = 0.88 on 2019-2021).
    "beemster": "Beemster_Dijkgraaf_de_Boer_boezempeil.xlsx",   # 2019-05 .. now
    "purmer_zuid": "Purmer_Purmer_Zuid_Boezempeil.xlsx",        # 2015-09 .. 2021-12 (ended)
}


def despike(series: pd.Series, window: str = "3h", tolerance: float = 0.05) -> pd.Series:
    """Drop 15-minute values more than ``tolerance`` m from their centred running median."""
    median = series.rolling(window, center=True, min_periods=3).median()
    return series[(series - median).abs() <= tolerance]


def cache_boezem(name: str) -> Path:
    """Daily mean of one despiked HHNK boezem gauge -> ``data/stresses/boezem_<name>_daily.csv``.

    The workbooks (CET/CEST, ``waterhoogte``) have up to 0.5 M rows; the fits read the CSV.
    """
    frame = pd.read_excel(STRESSES / BOEZEM_FILES[name])
    frame.columns = ["time", "level"]
    series = pd.Series(pd.to_numeric(frame["level"], errors="coerce").to_numpy(),
                       index=pd.to_datetime(frame["time"], errors="coerce")).dropna()
    series = series[series.index.notna()].sort_index()
    # The Schermerboezem is held at about -0.50 m NAP; outside -0.80 .. -0.20 is a sensor error.
    series = despike(series[series.between(-0.80, -0.20)])
    daily = series.resample("D").agg(["mean", "count"])
    daily = daily.loc[daily["count"] >= 48, "mean"]  # at least half a day of 15-minute values
    path = STRESSES / f"boezem_{name}_daily.csv"
    daily.rename("level_m_nap").rename_axis("date").to_frame().to_csv(path)
    return path


def boezem_level(name: str = "beemster") -> pd.Series:
    """The Dijkgraaf de Boer / Purmer Zuid gauges of 2026-11-23. Their files were removed on
    2026-11-24 (superseded by the pumping-station records below); this raises if they are gone."""
    path = STRESSES / f"boezem_{name}_daily.csv"
    if not path.exists():
        cache_boezem(name)
    frame = pd.read_csv(path, parse_dates=["date"], index_col="date")
    return regular(frame["level_m_nap"]).rename(f"boezem_{name}")


# --- pumping stations (gemalen): polder side and boezem side ------------------------
#
# HHNK 15-minute records (CET/CEST wall clock, m NAP), converted from the delivered xlsx to
# ``data/stresses/<Polder>_<Station>.csv`` with columns ``time, instroompeil, uitstroompeil``:
# ``instroompeil`` is the polder side (the pump inlet, the polder water level) and
# ``uitstroompeil`` the boezem side (the outlet into the Schermerboezem). Huygendijk's
# original columns were ``waterhoogte_krooshek`` (inlet) and ``waterhoogte_boezem``.
# Locations: OpenStreetMap / Nominatim (address of the station), good to a few hundred m.
GEMALEN = {
    "jacobus_bouman": ("Beemster", "Beemster_Jacobus_Bouman.csv", 52.5614, 4.9759),     # Oostdijk, east
    "wouter_sluis": ("Beemster", "Beemster_Wouter_Sluis.csv", 52.5683, 4.8636),         # Westdijk, west
    "purmer_stedelijk": ("Purmer", "Purmer_Stedelijk.csv", None, None),                 # the only Purmer record
    "hensbroek": ("Heerhugowaard", "Heerhugowaard_Hensbroek.csv", 52.6535, 4.8677),    # north-east
    "huygendijk": ("Heerhugowaard", "Heerhugowaard_Huygendijk.csv", 52.6365, 4.8012),  # south-west
}
COLUMNS = ("instroompeil", "uitstroompeil")
# Cleaning, the same for every record and column (see notebooks/exploration/gemaal_peilen.ipynb):
BAND_M = 0.40        # a value more than 0.40 m from the record's median is a sensor error
SPIKE_M = 0.10       # ... and so is one more than 0.10 m from its centred 3-hour running median
FLAT_VALUES = 192    # ... and a run of identical values longer than 2 days (192 x 15 min)
MIN_PER_DAY = 48     # a daily mean needs at least half a day of 15-minute values


def gemaal_raw(name: str) -> pd.DataFrame:
    """The converted 15-minute record, as delivered (unsorted, duplicates kept)."""
    return pd.read_csv(STRESSES / GEMALEN[name][1], parse_dates=["time"])


def clean_gemaal(raw: pd.Series) -> tuple[pd.Series, pd.Series]:
    """``(kept, removed)`` for one 15-minute column; ``removed`` has ``reason`` and ``value`` per stamp.

    1. missing values dropped; duplicate stamps averaged (the CET/CEST fall-back hour, and
       Huygendijk's 2018-2020, which is in the file twice with identical values);
    2. ``band``: further than ``BAND_M`` from the median (e.g. Purmer's boezem column reading
       the polder level, 4-13 Feb 2026);
    3. ``spike``: further than ``SPIKE_M`` from the centred 3-hour running median;
    4. ``flat``: inside a run of more than ``FLAT_VALUES`` identical values (a frozen sensor).
    """
    series = raw.dropna()
    series = series.groupby(level=0).mean().sort_index()
    reason = pd.Series(pd.NA, index=series.index, dtype="object")
    reason[(series - series.median()).abs() > BAND_M] = "band"
    ok = reason.isna()
    median = series[ok].rolling("3h", center=True, min_periods=3).median()
    spike = (series[ok] - median).abs() > SPIKE_M
    reason[spike[spike].index] = "spike"
    ok = reason.isna()
    same = series[ok].diff().eq(0)
    run_id = (~same).cumsum()
    run_len = run_id.map(run_id.value_counts())
    reason[run_len[run_len > FLAT_VALUES].index] = "flat"
    ok = reason.isna()
    return series[ok], reason[~ok].astype(str).rename("reason").to_frame().assign(value=series[~ok])


def gemaal_daily(name: str, refresh: bool = False) -> pd.DataFrame:
    """Cleaned daily means of both columns -> ``data/stresses/gemaal_<name>_daily.csv`` (cached)."""
    path = STRESSES / f"gemaal_{name}_daily.csv"
    if path.exists() and not refresh:
        return pd.read_csv(path, parse_dates=["date"], index_col="date")
    raw = gemaal_raw(name).set_index("time")
    out = {}
    for column in COLUMNS:
        kept, _ = clean_gemaal(raw[column])
        daily = kept.resample("D").agg(["mean", "count"])
        out[column] = daily.loc[daily["count"] >= MIN_PER_DAY, "mean"]
    frame = pd.DataFrame(out).rename_axis("date")
    frame.round(4).to_csv(path)
    return frame


def gemaal_level(name: str, column: str) -> pd.Series:
    return regular(gemaal_daily(name)[column]).rename(f"{name}_{column}")


# --- where a series sits: dike, cross-section position, nearest station --------------

DIKES = {"BE": "Beemster", "PU": "Purmer", "HH": "Heerhugowaard", "SC": "Schermer"}
# Crest (buitenkruin, kruin, binnenkruin) against berm and toe (binnenberm, insteek/kruin
# binnenberm, binnenteen). The crest is closer to the boezem, the toe to the polder ditch.
UPPER = ("BUKR", "KR", "BIKR")
LOWER = ("BIB", "INBIB", "KRBIB", "BIT", "BITA", "INST", "INSD", "BIRK")


def positions() -> pd.DataFrame:
    """Per series: ``dike`` (polder), ``pos`` (BUKR, BIT, ...), ``level`` (upper/lower), lat, lon.

    From ``object_data.csv`` ``meetlocatie_code``, e.g. ``_86349_MB028_(B_BE0213+10_BUKR_GMW)...``
    or ``2424-258424_B01_HH0259+67_BUKR_GMW_PB1``.
    """
    frame = pd.read_csv(OBJECT_DATA, usecols=["meetreeks_id", "meetlocatie_code", "lat", "lon"])
    parts = frame["meetlocatie_code"].astype(str).str.extract(r"(?P<dike>[A-Z]{2})\d+\+\d+_(?P<pos>[A-Z]+)_")
    frame = pd.concat([frame, parts], axis=1)
    frame["polder"] = frame["dike"].map(DIKES)
    frame["level"] = frame["pos"].map(lambda p: "upper" if p in UPPER else "lower" if p in LOWER else pd.NA)
    return frame.drop_duplicates("meetreeks_id").set_index("meetreeks_id")


def stations_by_distance(lat: float, lon: float, polder: str) -> list[tuple[str, float]]:
    """The polder's stations, nearest first, as ``(name, km)``; a station without a location
    (the polder's only one) gets distance NaN."""
    out = []
    for name, (where, _, s_lat, s_lon) in GEMALEN.items():
        if where != polder:
            continue
        km = distance_km(lat, lon, s_lat, s_lon) if s_lat is not None and pd.notna(lat) else float("nan")
        out.append((name, km))
    return sorted(out, key=lambda item: (math.isnan(item[1]), item[1]))


def polder_points() -> dict[str, tuple[float, float]]:
    """One point per polder (the centroid of its piezometers): where the radar pixel is read."""
    frame = positions().dropna(subset=["polder", "lat", "lon"])
    frame = frame[frame["polder"].isin(["Beemster", "Purmer", "Heerhugowaard"])]
    return {p: (round(float(g["lat"].mean()), 4), round(float(g["lon"].mean()), 4))
            for p, g in frame.groupby("polder")}


def setpoint_deviation(series: pd.Series, days: int = 31) -> pd.Series:
    """The level minus its centred running median: what is left after the managed set point."""
    setpoint = series.rolling(days, center=True, min_periods=days // 2).median()
    return (series - setpoint).rename(f"{series.name}_dev")


def polders() -> dict[str, str]:
    """``meetreeks_id -> project`` (Purmer, Beemster, ...) via the boxplots config."""
    config = pd.read_csv(REPO / "boxplots" / "input_data" / "config.csv",
                         usecols=["LD_project", "DD_meetlocatie_id"]).drop_duplicates()
    objects = pd.read_csv(OBJECT_DATA, usecols=["meetreeks_id", "meetlocatie_object_id"])
    both = objects.merge(config, left_on="meetlocatie_object_id", right_on="DD_meetlocatie_id")
    return dict(zip(both["meetreeks_id"], both["LD_project"]))


def regular(series: pd.Series, limit: int = 7) -> pd.Series:
    """On every day (Pastas needs a regular index); gaps up to ``limit`` days interpolated,
    longer ones left NaN for Pastas' own ``fill_nan``."""
    series = series.dropna()
    return series.asfreq("D").interpolate(limit=limit, limit_area="inside")


def anomaly(series: pd.Series, around: pd.Series | None = None) -> pd.Series:
    """The stress minus its mean over the calibration days (or over the whole series)."""
    base = series if around is None else series.loc[around.index.min():around.index.max()]
    return series - float(base.mean())
