"""Loading the bias-corrected IMPREX ensemble forcing (handoff section 3).

16 member pairs of daily CSVs from the KNMI-RACMO-ECEARTH historical run,
1951-2000, one grid cell. Each member is an independent, physically consistent
realisation of the same 50 years of climate.

``imprex_<location>_ens<NN>_daily_precipitation.csv``   date, precipitation_mm
``imprex_<location>_ens<NN>_daily_evaporation.csv``     date, makkink_mm

**These files are already bias-corrected.** The raw RACMO output drizzles -- it
rains too often and too gently -- and groundwater head integrates recharge over
weeks, so that bias does not average out. The correction has been applied and
baked in; do not apply another. Provenance is in ``bias_correction_run_log.json``
beside the files.

Members are discovered by globbing and pairing, never assumed to be 1..16: a
member missing its evaporation partner is excluded rather than half-loaded.
"""

from __future__ import annotations

import glob
import os
import re
from dataclasses import dataclass

import pandas as pd

PREC_COLUMN = "precipitation_mm"
EVAP_COLUMN = "makkink_mm"
DATE_COLUMN = "date"

_MEMBER_RE = re.compile(r"_ens(\d+)_daily_precipitation\.csv$", re.IGNORECASE)


@dataclass(frozen=True)
class Member:
    """One ensemble member: a precipitation file and its evaporation partner."""

    member: str  # "ens01"
    precipitation_file: str
    evaporation_file: str


def _prec_name(directory: str, location: str, member: str) -> str:
    return os.path.join(directory, f"imprex_{location}_{member}_daily_precipitation.csv")


def _evap_name(directory: str, location: str, member: str) -> str:
    return os.path.join(directory, f"imprex_{location}_{member}_daily_evaporation.csv")


def discover_members(directory: str, location: str = "berkhout") -> list[Member]:
    """Find every member that has **both** files, sorted by member number.

    Globbing rather than assuming ens01..ens16 means a partial upload shows up as
    fewer members in the summary instead of a mid-run file-not-found.
    """
    pattern = os.path.join(directory, f"imprex_{location}_ens*_daily_precipitation.csv")
    found: list[Member] = []
    for prec_path in sorted(glob.glob(pattern)):
        match = _MEMBER_RE.search(os.path.basename(prec_path))
        if not match:
            continue
        member = f"ens{match.group(1)}"
        evap_path = _evap_name(directory, location, member)
        if os.path.isfile(evap_path):
            found.append(
                Member(
                    member=member,
                    precipitation_file=prec_path,
                    evaporation_file=evap_path,
                )
            )
    return found


def _read_one(path: str, column: str) -> pd.Series:
    frame = pd.read_csv(path)
    if DATE_COLUMN not in frame.columns:
        raise ValueError(f"{path} has no '{DATE_COLUMN}' column; found {list(frame.columns)}")
    if column not in frame.columns:
        raise ValueError(f"{path} has no '{column}' column; found {list(frame.columns)}")

    index = pd.DatetimeIndex(pd.to_datetime(frame[DATE_COLUMN], errors="coerce"))
    if index.tz is not None:
        index = index.tz_convert(None)
    # Normalise to midnight: the models' own forcing index is date-aligned, and a
    # stray time component would misalign the two.
    index = index.normalize()

    series = pd.Series(
        pd.to_numeric(frame[column], errors="coerce").to_numpy(dtype=float), index=index
    )
    series = series[series.index.notna()].sort_index()
    series = series[~series.index.duplicated(keep="first")].dropna()
    series.name = column
    return series


def load_member(
    member: Member,
    units: str = "mm",
    prec_column: str = PREC_COLUMN,
    evap_column: str = EVAP_COLUMN,
) -> tuple[pd.Series, pd.Series]:
    """Read one member pair as (precipitation, evaporation).

    ``units`` must match whatever the models were calibrated on: the stresses are
    swapped into a fitted model whose gain parameters are on that scale, so
    loading these in millimetres when the model saw metres would rescale every
    simulated head. The caller passes stage one's ``forcing_units``.
    """
    from tarso_pipeline.forcing import UNIT_SCALES

    if units not in UNIT_SCALES:
        raise ValueError(f"Unknown units {units!r}; use one of {sorted(UNIT_SCALES)}")
    scale = UNIT_SCALES[units]

    precipitation = _read_one(member.precipitation_file, prec_column) * scale
    evaporation = _read_one(member.evaporation_file, evap_column) * scale
    precipitation.name = "prec"
    evaporation.name = "evap"
    return precipitation, evaporation
