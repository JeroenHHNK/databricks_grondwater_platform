"""Fetching daily precipitation and Makkink evaporation from the KNMI open API.

KNMI publishes two climatology products behind the same query interface:

``daggegevens``  daily values. The only product that carries **EV24**, the Makkink
                 reference evaporation. This module uses it.
``uurgegevens``  hourly values. Its ``PRCP`` set is ``DR:RH`` -- precipitation
                 duration and the hourly sum. There is **no Makkink evaporation
                 in the hourly product**, so it cannot serve this pipeline.

Both ``RH`` (daily precipitation sum) and ``EV24`` are published in **0.1 mm** and
are converted to mm here, because forcings are passed to Pastas in mm (see
``DATA_CATALOG.md`` section 3). ``RH == -1`` is KNMI's code for "less than
0.05 mm" and is read as 0.0.

No Spark and no state live here: one POST, one DataFrame. The incremental
bookkeeping is the notebook's job.
"""

from __future__ import annotations

import datetime as dt
import io
from dataclasses import dataclass

import pandas as pd
import requests

KNMI_DAILY_URL = "https://www.daggegevens.knmi.nl/klimatologie/daggegevens"

# KNMI station 249 Berkhout -- the forcing station named in DATA_CATALOG.md.
DEFAULT_STATION = 249

# The two variables this pipeline needs, mapped to the column names
# ``load_forcing()`` expects.
VAR_COLUMNS: dict[str, str] = {
    "RH": "precipitation_mm",
    "EV24": "makkink_mm",
}

# Every variable in VAR_COLUMNS is published in 0.1 mm.
_TENTHS_MM = frozenset(VAR_COLUMNS)

# Variables where -1 is the "below measuring threshold" code rather than a value.
_BELOW_THRESHOLD = frozenset({"RH"})

DATE_FORMAT = "%Y%m%d"

OUTPUT_COLUMNS = ("station", "date", "precipitation_mm", "makkink_mm")


@dataclass(frozen=True)
class FetchWindow:
    """The closed date range ``[start, end]`` to ask KNMI for."""

    start: dt.date
    end: dt.date

    @property
    def is_empty(self) -> bool:
        """True when there is nothing to ask for, so no request should be made."""
        return self.start > self.end

    def as_params(self) -> dict[str, str]:
        return {
            "start": self.start.strftime(DATE_FORMAT),
            "end": self.end.strftime(DATE_FORMAT),
        }


def plan_window(
    last_stored: dt.date | None,
    today: dt.date,
    backlog_start: dt.date,
    lookback_days: int = 2,
) -> FetchWindow:
    """Decide which dates still need fetching.

    ``last_stored``   newest date already in the target table, or None when the
                      table does not exist yet / is empty.
    ``backlog_start`` where the history starts on a first run (2020-01-01 here).
    ``lookback_days`` how far back before ``last_stored`` to re-fetch. KNMI revises
                      the most recent days once values are validated, so a small
                      overlap keeps stored values correct. Rows are written with a
                      MERGE, so re-fetching the same dates is idempotent.

    On a first run this spans the whole backlog. Afterwards it is a handful of
    days, and once the table is current ``is_empty`` is True and the caller skips
    the request entirely -- which is the point of the watermark.
    """
    if lookback_days < 0:
        raise ValueError(f"lookback_days must be >= 0, got {lookback_days}")
    if last_stored is None:
        return FetchWindow(start=backlog_start, end=today)
    start = last_stored + dt.timedelta(days=1) - dt.timedelta(days=lookback_days)
    # Never walk back past the start of the intended history.
    start = max(start, backlog_start)
    return FetchWindow(start=start, end=today)


def _column_names(text: str) -> list[str]:
    """Pull the column names out of the ``# STN,YYYYMMDD,   RH, EV24`` comment.

    KNMI echoes the variable list back in its own order rather than the requested
    one, so this header is the only reliable source for the column layout.
    """
    for line in text.splitlines():
        stripped = line.lstrip("#").strip()
        if stripped.startswith("STN,"):
            return [part.strip() for part in stripped.split(",")]
    raise ValueError(
        "No '# STN,...' header comment in the KNMI response; "
        "the response format changed or the request was rejected"
    )


def _empty_frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "station": pd.Series(dtype="Int64"),
            "date": pd.Series(dtype="datetime64[ns]"),
            "precipitation_mm": pd.Series(dtype="float64"),
            "makkink_mm": pd.Series(dtype="float64"),
        }
    )


def parse_daily_csv(text: str) -> pd.DataFrame:
    """Turn one KNMI CSV response into a tidy mm-per-day frame.

    Returns columns ``station``, ``date``, ``precipitation_mm``, ``makkink_mm``:
    one row per station per day, sorted, duplicates dropped. A response holding
    only comment lines -- what a window past the last published day returns --
    yields an empty frame with those columns rather than an error.
    """
    names = _column_names(text)
    data_lines = [
        line
        for line in text.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]

    if not data_lines:
        return _empty_frame()

    raw = pd.read_csv(
        io.StringIO("\n".join(data_lines)),
        names=names,
        skipinitialspace=True,
        # A blank field is KNMI's "missing"; it must become NaN, never 0.
        na_values=[""],
    )

    out = pd.DataFrame(
        {
            "station": pd.to_numeric(raw["STN"], errors="coerce").astype("Int64"),
            "date": pd.to_datetime(raw["YYYYMMDD"], format=DATE_FORMAT, errors="coerce"),
        }
    )

    for var, column in VAR_COLUMNS.items():
        if var not in raw.columns:
            # The response does not carry this variable; keep the column, all-NaN,
            # so the frame's shape never depends on the response.
            out[column] = pd.Series([float("nan")] * len(raw), dtype="float64")
            continue
        values = pd.to_numeric(raw[var], errors="coerce")
        if var in _BELOW_THRESHOLD:
            # -1 means "measured, but under 0.05 mm" -- a real dry day, not missing.
            values = values.mask(values == -1, 0.0)
        if var in _TENTHS_MM:
            values = values / 10.0
        out[column] = values.astype("float64")

    out = out[out["date"].notna() & out["station"].notna()]
    out = out.sort_values(["station", "date"]).drop_duplicates(
        subset=["station", "date"], keep="last"
    )
    return out.reset_index(drop=True)[list(OUTPUT_COLUMNS)]


def fetch_daily(
    window: FetchWindow,
    station: int = DEFAULT_STATION,
    timeout: int = 120,
    session: requests.Session | None = None,
) -> pd.DataFrame:
    """Fetch ``[window.start, window.end]`` for one station as mm per day.

    An empty window short-circuits: no request is sent at all. That is what keeps
    a scheduled run from re-pulling history it already has.
    """
    if window.is_empty:
        return _empty_frame()

    params = {
        **window.as_params(),
        "vars": ":".join(VAR_COLUMNS),
        "stns": str(station),
    }
    post = session.post if session is not None else requests.post
    response = post(KNMI_DAILY_URL, data=params, timeout=timeout)
    response.raise_for_status()
    return parse_daily_csv(response.text)
