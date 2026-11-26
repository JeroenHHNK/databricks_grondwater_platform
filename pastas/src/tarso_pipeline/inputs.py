"""What a model is driven by: the precipitation and evaporation source, and extra stresses.

Every model type (``tarso_pipeline.fit.MODEL_SPECS``) says *which kind* of forcing it
wants: the KNMI station (Berkhout 249 by default) or the KNMI radar pixel of the series'
polder, and which extra stresses (Markermeer level, air pressure). ``InputLibrary`` turns
that into the actual series for one series, and records where they came from, so every
fit row says ``precipitation_source`` / ``evaporation_source`` / ``extra_stresses``.

Files (all in the forcing folder, the same one as ``knmi_forcing_<station>.csv``)
--------------------------------------------------------------------------------
``knmi_forcing_<station>.csv``     date, precipitation_mm, makkink_mm (KNMI daily).
``radar_forcing_<polder>.csv``     date, precipitation_mm, makkink_mm, precipitation_source:
                                   KNMI gauge-adjusted daily radar (``rad_nl25_rac_mfbs_24h``)
                                   at one pixel per polder; the 08-08 UTC sums moved one day
                                   back. Before and after the radar record (warm-up, the
                                   month the product lags) it is the station's
                                   precipitation, marked per day in ``precipitation_source``.
                                   Evaporation is the station's Makkink.
``stress_<name>.csv``              date, value: ``lake_edam`` (RWS Markermeer at Edam,
                                   m NAP, daily mean) and ``pressure_240`` (KNMI 240
                                   Schiphol, daily mean sea-level pressure, hPa).

The ``bucket`` stress is not a file: it is the deficit of a root-zone soil-moisture bucket
(``bucket_deficit``, Smax 150 mm, ET reduced below 70 % full) run on the model's own
precipitation and evaporation. So on IMPREX it is rebuilt from each member's P and E
(``InputLibrary.member_forcing``) instead of being held at its mean
(``docs/experiments/2026-11-24_drought.md``).

Built by ``scripts/build_forcing_sources.py``. On Databricks the folder is the
``forcing_dir`` volume; a file missing there is read from the repo's ``data/forcing``.

Which polder a series is in comes from ``object_data.csv`` (the dike code in
``meetlocatie_code``: BE Beemster, PU Purmer, HH Heerhugowaard). A series without a
polder that has radar gets no radar model types.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from tarso_pipeline.forcing import UNIT_SCALES, forcing_filename, load_forcing_pair
from tarso_pipeline.settings import load_settings

RADAR_POLDERS = ("beemster", "purmer", "heerhugowaard")
DIKE_POLDER = {"BE": "beemster", "PU": "purmer", "HH": "heerhugowaard"}
STRESS_FILES = {"lake": "stress_lake_edam.csv", "pressure": "stress_pressure_240.csv"}
OBJECT_DATA = Path("data_validation") / "input_output" / "output_databricks" / "metadata" / "object_data.csv"
# The soil-moisture bucket: one setting for every series ([bucket] in
# config/model_settings.toml), chosen on the validated Beemster/Purmer series with the
# 2026 drought left out of the fit.
BUCKET_SMAX_MM = float(load_settings().bucket.smax_mm)
BUCKET_LP = float(load_settings().bucket.lp)
# extra_stresses says which bucket: bucket_<Smax>_<lp without the point>, e.g. bucket_150_07
STRESS_SOURCES = {"lake": "lake_edam", "pressure": "pressure_240",
                  "bucket": f"bucket_{BUCKET_SMAX_MM:g}_{f'{BUCKET_LP:g}'.replace('.', '')}"}
DERIVED_STRESSES = ("bucket",)  # computed from the model's P and E, not read from a file


def bucket_deficit(precipitation_mm: pd.Series, evaporation_mm: pd.Series, smax: float = BUCKET_SMAX_MM,
                   lp: float = BUCKET_LP, balance: bool = False):
    """The deficit ``Smax - S`` (mm) of a root-zone bucket run on daily P and E in mm/day.

    The bucket starts full. Each day rain goes in, the plants take
    ``E * min(1, S / (lp * Smax))`` (less once the soil is below ``lp`` full, never more
    than what is there), and whatever is above ``Smax`` overflows. With ``balance`` it also
    returns the storage, actual ET and overflow per day.
    """
    import numpy as np

    precipitation_mm, evaporation_mm = precipitation_mm.align(evaporation_mm, join="inner")
    p = precipitation_mm.fillna(0.0).to_numpy(dtype=float)
    e = evaporation_mm.fillna(0.0).to_numpy(dtype=float)
    storage, actual, overflow = np.empty(len(p)), np.empty(len(p)), np.empty(len(p))
    s = smax
    for i in range(len(p)):
        taken = min(e[i] * min(1.0, s / (lp * smax)), s + p[i])
        s = s + p[i] - taken
        spill = max(0.0, s - smax)
        s -= spill
        storage[i], actual[i], overflow[i] = s, taken, spill
    deficit = pd.Series(smax - storage, index=precipitation_mm.index, name="bucket_deficit")
    if balance:
        return deficit, pd.DataFrame({"storage": storage, "actual_et": actual, "overflow": overflow},
                                     index=precipitation_mm.index)
    return deficit


def derived_stress(name: str, precipitation: pd.Series, evaporation: pd.Series, units: str = "m") -> pd.Series:
    """A stress computed from P and E in the run's ``units`` (``bucket``: the deficit in mm)."""
    if name != "bucket":
        raise ValueError(f"unknown derived stress {name!r}; use one of {DERIVED_STRESSES}")
    to_mm = 1.0 / UNIT_SCALES[units]
    return bucket_deficit(precipitation * to_mm, evaporation * to_mm)


def radar_filename(polder: str) -> str:
    return f"radar_forcing_{polder}.csv"


def source_label(source: str | None) -> str:
    """``knmi_249`` -> "KNMI 249 Berkhout"; ``radar_purmer`` -> "KNMI radar (Purmer)"."""
    if source is None or (isinstance(source, float) and pd.isna(source)) or source == "":
        return "–"
    source = str(source)
    names = {"249": "Berkhout", "240": "Schiphol", "225": "IJmuiden"}
    if source.startswith("radar_"):
        return f"KNMI radar ({source[6:].capitalize()})"
    if source.startswith("knmi_"):
        station = source[5:]
        return f"KNMI {station} {names.get(station, '')}".strip()
    if source.isdigit():
        return f"KNMI {source} {names.get(source, '')}".strip()
    return source


def polder_of(meetlocatie_code) -> str | None:
    """``_86349_MB028_(B_BE0213+10_BUKR_GMW)...`` -> "beemster"; None when it has no dike code."""
    import re

    match = re.search(r"([A-Z]{2})\d+\+\d+_", str(meetlocatie_code))
    return DIKE_POLDER.get(match.group(1)) if match else None


def read_polders(object_data: str | Path) -> dict[str, str]:
    """``meetreeks_id -> polder`` for every series with a radar polder."""
    frame = pd.read_csv(object_data, usecols=["meetreeks_id", "meetlocatie_code"]).drop_duplicates("meetreeks_id")
    out = {}
    for row in frame.itertuples():
        polder = polder_of(row.meetlocatie_code)
        if polder:
            out[row.meetreeks_id] = polder
    return out


def read_stress(path: str | Path) -> pd.Series:
    frame = pd.read_csv(path, parse_dates=["date"]).set_index("date").sort_index()
    series = pd.to_numeric(frame["value"], errors="coerce").dropna()
    return series[~series.index.duplicated()].asfreq("D")


def monthly_factors(local: pd.Series, reference: pd.Series) -> pd.Series:
    """Mean per calendar month, local over reference, on the days both have (index 1..12)."""
    both = pd.concat([local, reference], axis=1, keys=["local", "ref"]).dropna()
    means = both.groupby(both.index.month).mean()
    return (means["local"] / means["ref"]).reindex(range(1, 13)).fillna(1.0)


@dataclass
class Inputs:
    """The forcing and stresses of one model of one series, and where they came from."""

    precipitation: pd.Series
    evaporation: pd.Series
    precipitation_source: str
    evaporation_source: str
    stresses: dict = field(default_factory=dict)   # name -> series in its own units

    @property
    def extra_stresses(self) -> str | None:
        return ",".join(STRESS_SOURCES[k] for k in self.stresses) or None


class InputLibrary:
    """Loads each forcing file once and hands out ``Inputs`` per (model type, series).

    ``directory`` is the forcing folder; ``fallback`` (the repo's ``data/forcing``) is
    used for a file that is not there. ``polders`` maps meetreeks_id -> polder.
    """

    def __init__(self, directory: str | Path, station: str = "249", units: str = "m",
                 fallback: str | Path | None = None, polders: dict | None = None):
        self.directory, self.fallback = Path(directory), Path(fallback) if fallback else None
        self.station, self.units = str(station), units
        self.polders = polders or {}
        self._cache: dict = {}

    def path(self, filename: str) -> Path:
        here = self.directory / filename
        if here.is_file() or self.fallback is None:
            return here
        return self.fallback / filename

    def _pair(self, filename: str):
        if filename not in self._cache:
            self._cache[filename] = load_forcing_pair(str(self.path(filename)), units=self.units)
        return self._cache[filename]

    def station_forcing(self):
        return self._pair(forcing_filename(self.station))

    def radar_forcing(self, polder: str):
        return self._pair(radar_filename(polder))

    def stress(self, name: str) -> pd.Series:
        key = f"stress:{name}"
        if key not in self._cache:
            self._cache[key] = read_stress(self.path(STRESS_FILES[name]))
        return self._cache[key]

    def radar_polder(self, meetreeks_id: str) -> str | None:
        polder = self.polders.get(meetreeks_id)
        return polder if polder and self.path(radar_filename(polder)).is_file() else None

    def applicable(self, model_type: str, meetreeks_id: str) -> bool:
        """A radar model type needs a series in a polder with a radar file."""
        from tarso_pipeline.fit import model_spec

        return not model_spec(model_type).radar or self.radar_polder(meetreeks_id) is not None

    def inputs(self, model_type: str, meetreeks_id: str) -> Inputs:
        from tarso_pipeline.fit import model_spec

        spec = model_spec(model_type)
        station = f"knmi_{self.station}"
        if spec.radar:
            polder = self.radar_polder(meetreeks_id)
            if polder is None:
                raise ValueError(f"no radar forcing for series {meetreeks_id[:12]} (polder unknown or no file)")
            precipitation, evaporation = self.radar_forcing(polder)
            precipitation_source = f"radar_{polder}"
        else:
            precipitation, evaporation = self.station_forcing()
            precipitation_source = station
        stresses = {name: (derived_stress(name, precipitation, evaporation, self.units)
                           if name in DERIVED_STRESSES else self.stress(name)) for name in spec.stresses}
        return Inputs(precipitation, evaporation, precipitation_source, station, stresses)

    def member_forcing(self, model_type: str, meetreeks_id: str, forcing: dict) -> dict:
        """The IMPREX members as this model needs them: ``member -> (P, E[, synthetic])``.

        A radar model gets P scaled per month (``imprex_factors``); a model with a derived
        stress (the bucket) gets it rebuilt from each member's own, scaled P and E, keyed by
        its stress model name, for ``simulate.simulate_member(synthetic=...)``.
        """
        from tarso_pipeline.fit import STRESS_NAMES, model_spec

        scaled = scale_members(forcing, self.imprex_factors(model_type, meetreeks_id))
        derived = [name for name in model_spec(model_type).stresses if name in DERIVED_STRESSES]
        if not derived:
            return scaled
        return {member: (p, e, {STRESS_NAMES[name]: derived_stress(name, p, e, self.units) for name in derived})
                for member, (p, e, *_) in scaled.items()}

    def imprex_factors(self, model_type: str, meetreeks_id: str) -> pd.Series | None:
        """Per calendar month, radar over station precipitation; None for a station model.

        IMPREX is bias-corrected to the station. A model calibrated on radar (about 10 %
        wetter) gets members scaled by these factors, so its climate matches its
        calibration forcing.
        """
        from tarso_pipeline.fit import model_spec

        if not model_spec(model_type).radar:
            return None
        polder = self.radar_polder(meetreeks_id)
        if polder is None:
            return None
        frame = pd.read_csv(self.path(radar_filename(polder)), parse_dates=["date"]).set_index("date")
        radar = frame.loc[frame["precipitation_source"].astype(str).str.startswith("radar"), "precipitation_mm"]
        station, _ = self.station_forcing()
        return monthly_factors(radar * UNIT_SCALES[self.units], station)


def scale_members(forcing: dict, factors: pd.Series | None) -> dict:
    """``member -> (P, E)`` with P multiplied by the factor of its calendar month."""
    if factors is None:
        return forcing
    out = {}
    for member, (precipitation, evaporation, *rest) in forcing.items():
        scaled = precipitation * factors.reindex(precipitation.index.month).to_numpy()
        out[member] = (scaled, evaporation, *rest)
    return out
