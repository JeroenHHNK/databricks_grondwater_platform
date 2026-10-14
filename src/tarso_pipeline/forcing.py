"""Loading the KNMI forcing CSV and checking it covers the calibration window.

The file is a CSV on a volume rather than a table, because it is small and feeds
Pastas directly. One station stands in for per-series forcing for now, so the
**station is a parameter and the filename is derived from it** -- swapping in
per-series stations later is a change of argument, not a rewrite.

Units
-----
KNMI publishes millimetres per day. Pastas is given **metres per day** by default
here (``units="m"``, i.e. mm / 1000), matching the head, which is in metres NAP.

This differs from the original repo, which passed millimetres straight through.
The choice only rescales the gain parameters, but it makes fits from the two
conventions incomparable, so ``units`` is recorded on every metrics row. Pass
``units="mm"`` to reproduce the original behaviour.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

# Multiplier applied to the millimetre values in the file.
UNIT_SCALES: dict[str, float] = {"mm": 1.0, "m": 0.001}

DEFAULT_UNITS = "m"
PREC_COLUMN = "precipitation_mm"
EVAP_COLUMN = "makkink_mm"
DATE_COLUMN = "date"


def forcing_filename(station: int | str) -> str:
    """``knmi_forcing_<station>.csv`` -- derived, never hardcoded."""
    return f"knmi_forcing_{station}.csv"


def forcing_file(directory: str, station: int | str) -> str:
    """Full path of one station's forcing file inside ``directory``."""
    return f"{str(directory).rstrip('/')}/{forcing_filename(station)}"


def load_forcing_pair(
    path: str,
    units: str = DEFAULT_UNITS,
    prec_column: str = PREC_COLUMN,
    evap_column: str = EVAP_COLUMN,
) -> tuple[pd.Series, pd.Series]:
    """Read one forcing CSV into (precipitation, evaporation), scaled.

    Both series come back sorted, duplicate-free, with a tz-naive
    ``DatetimeIndex`` and no NaN -- Pastas must not receive gaps it did not ask
    for.
    """
    if units not in UNIT_SCALES:
        raise ValueError(f"Unknown units {units!r}; use one of {sorted(UNIT_SCALES)}")

    frame = pd.read_csv(path)
    missing = [c for c in (DATE_COLUMN, prec_column, evap_column) if c not in frame.columns]
    if missing:
        raise ValueError(
            f"{path} is missing column(s) {missing}. Expected "
            f"{[DATE_COLUMN, prec_column, evap_column]}, found {list(frame.columns)}"
        )

    index = pd.DatetimeIndex(pd.to_datetime(frame[DATE_COLUMN], errors="coerce"))
    if index.tz is not None:
        index = index.tz_convert(None)

    scale = UNIT_SCALES[units]
    out = []
    for column, name in ((prec_column, "prec"), (evap_column, "evap")):
        series = pd.Series(
            pd.to_numeric(frame[column], errors="coerce").to_numpy(dtype=float),
            index=index,
        )
        series = series[series.index.notna()].sort_index()
        series = series[~series.index.duplicated(keep="first")].dropna()
        series = series * scale
        series.name = name
        out.append(series)
    return out[0], out[1]


@dataclass(frozen=True)
class Coverage:
    """Whether the forcing reaches far enough back for the requested warmup."""

    forcing_start: pd.Timestamp
    forcing_end: pd.Timestamp
    head_start: pd.Timestamp
    head_end: pd.Timestamp
    requested_warmup_days: int
    available_warmup_days: int
    covers_calibration: bool

    @property
    def warmup_ok(self) -> bool:
        return self.available_warmup_days >= self.requested_warmup_days

    @property
    def usable_warmup_days(self) -> int:
        """The warmup to actually ask for: the requested one, or all there is."""
        return min(self.requested_warmup_days, max(self.available_warmup_days, 0))

    def describe(self) -> str:
        """A sentence saying what will happen, rather than leaving it implicit."""
        if not self.covers_calibration:
            return (
                f"FORCING DOES NOT COVER THE HEAD: forcing runs "
                f"{self.forcing_start.date()} to {self.forcing_end.date()}, head runs "
                f"{self.head_start.date()} to {self.head_end.date()}. Trim the "
                f"calibration window or use a station with longer history."
            )
        if self.warmup_ok:
            return (
                f"warmup OK: {self.available_warmup_days} days of forcing before the "
                f"head starts, {self.requested_warmup_days} requested"
            )
        return (
            f"WARMUP SHORTENED to {self.usable_warmup_days} days "
            f"({self.requested_warmup_days} requested): forcing starts "
            f"{self.forcing_start.date()}, head starts {self.head_start.date()}"
        )


def check_coverage(
    forcing: pd.Series,
    head: pd.Series,
    warmup_days: int = 3650,
) -> Coverage:
    """Compare the forcing extent with a head series plus its warmup.

    Pastas needs forcing over the calibration period *and* the warmup before it.
    Returning this explicitly is the point: the caller shortens the warmup or
    trims the calibration window deliberately, rather than letting Pastas pick
    silently.
    """
    forcing_start = pd.Timestamp(forcing.index.min())
    forcing_end = pd.Timestamp(forcing.index.max())
    head_start = pd.Timestamp(head.index.min())
    head_end = pd.Timestamp(head.index.max())

    available = (head_start - forcing_start).days
    covers = forcing_start <= head_start and forcing_end >= head_end

    return Coverage(
        forcing_start=forcing_start,
        forcing_end=forcing_end,
        head_start=head_start,
        head_end=head_end,
        requested_warmup_days=warmup_days,
        available_warmup_days=available,
        covers_calibration=covers,
    )
