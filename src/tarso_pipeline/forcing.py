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
    """How the forcing extent lines up with a head series and its warmup.

    The two ends fail for different reasons and need different answers, so they
    are reported separately:

    **The start** governs the warmup. Forcing that begins after
    ``head_start - warmup`` means a shorter warmup; a different station with
    deeper history can fix it.

    **The end** governs the calibration window. KNMI publishes one to two days
    behind, so a series running to today *always* ends after the forcing. That is
    normal and the fix is to trim ``tmax`` by a couple of days -- not to go
    looking for another station, since every station lags.
    """

    forcing_start: pd.Timestamp
    forcing_end: pd.Timestamp
    head_start: pd.Timestamp
    head_end: pd.Timestamp
    requested_warmup_days: int
    available_warmup_days: int

    @property
    def covers_start(self) -> bool:
        return self.forcing_start <= self.head_start

    @property
    def covers_end(self) -> bool:
        return self.forcing_end >= self.head_end

    @property
    def covers_calibration(self) -> bool:
        return self.covers_start and self.covers_end

    @property
    def warmup_ok(self) -> bool:
        return self.available_warmup_days >= self.requested_warmup_days

    @property
    def usable_warmup_days(self) -> int:
        """The warmup to actually ask for: the requested one, or all there is."""
        return min(self.requested_warmup_days, max(self.available_warmup_days, 0))

    @property
    def tmin(self) -> pd.Timestamp:
        """Calibration start: the head's, pushed later only if forcing demands."""
        return max(self.head_start, self.forcing_start)

    @property
    def tmax(self) -> pd.Timestamp:
        """Calibration end: the head's, trimmed back to where the forcing ends."""
        return min(self.head_end, self.forcing_end)

    @property
    def days_trimmed_from_end(self) -> int:
        """Head days dropped because the forcing stops earlier. Usually 1-2."""
        return max((self.head_end - self.forcing_end).days, 0)

    @property
    def trimmed(self) -> bool:
        return self.tmin != self.head_start or self.tmax != self.head_end

    @property
    def usable(self) -> bool:
        """Is there any overlap left to calibrate on at all?"""
        return self.tmin < self.tmax

    def describe(self) -> str:
        """A sentence per decision, so nothing is resolved silently."""
        parts: list[str] = []

        if not self.usable:
            return (
                f"NO OVERLAP: forcing {self.forcing_start.date()}.."
                f"{self.forcing_end.date()} and head {self.head_start.date()}.."
                f"{self.head_end.date()} do not overlap. Nothing can be calibrated."
            )

        if self.warmup_ok:
            parts.append(
                f"warmup OK ({self.available_warmup_days} days available, "
                f"{self.requested_warmup_days} requested)"
            )
        else:
            parts.append(
                f"WARMUP SHORTENED to {self.usable_warmup_days} days "
                f"({self.requested_warmup_days} requested; forcing starts "
                f"{self.forcing_start.date()}, head starts {self.head_start.date()}) "
                f"- a station with deeper history would fix this"
            )

        if self.days_trimmed_from_end:
            parts.append(
                f"calibration trimmed to {self.tmax.date()}, dropping "
                f"{self.days_trimmed_from_end} head day(s) past the forcing "
                f"(KNMI publishes 1-2 days behind; refresh the forcing to recover them)"
            )
        if not self.covers_start:
            parts.append(f"calibration starts at {self.tmin.date()}, not the head's start")

        return "; ".join(parts)


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

    return Coverage(
        forcing_start=forcing_start,
        forcing_end=forcing_end,
        head_start=head_start,
        head_end=head_end,
        requested_warmup_days=warmup_days,
        available_warmup_days=(head_start - forcing_start).days,
    )
