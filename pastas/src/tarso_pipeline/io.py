"""Reading head (groundwater level) CSVs and meteorological forcing CSVs.

Everything here takes a plain filesystem path. On Databricks, Unity Catalog
Volumes are mounted as ordinary paths (``/Volumes/<catalog>/<schema>/<volume>/...``),
so no Spark is needed to read a single file inside a worker.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

# Columns that make a file "validated". ALL three must be present (case-insensitive);
# a partial match falls back to raw.
VALIDATED_COLUMNS = ("raw_data", "gecontroleerd", "afgekeurd")

# Names a time column may have. Matched case-insensitively; otherwise column 0 is used.
TIME_COLUMN_CANDIDATES = ("time", "timestamp", "datetime", "date", "tijd")


class HeadFormat(str, enum.Enum):
    RAW = "raw"
    VALIDATED = "validated"


@dataclass(frozen=True)
class LoadedHead:
    """Result of reading one head CSV."""

    series: pd.Series  # DatetimeIndex, sorted, float, no NaN
    format: HeadFormat
    n_rows_read: int  # data rows in the file
    n_rejected: int  # rows dropped by the validation filter (0 for raw)


def _read_csv(path: str | Path) -> pd.DataFrame:
    # utf-8-sig strips the BOM that validated files carry, so the first column
    # name is not polluted with a leading U+FEFF.
    df = pd.read_csv(path, encoding="utf-8-sig", skipinitialspace=True)
    df.columns = [str(c).strip() for c in df.columns]
    return df


def _find_column(df: pd.DataFrame, name: str) -> str | None:
    """Case-insensitive, whitespace-insensitive column lookup; returns the real name."""
    wanted = name.strip().lower()
    for col in df.columns:
        if col.strip().lower() == wanted:
            return col
    return None


def _time_column(df: pd.DataFrame) -> str:
    for candidate in TIME_COLUMN_CANDIDATES:
        col = _find_column(df, candidate)
        if col is not None:
            return col
    return df.columns[0]


def detect_format(df: pd.DataFrame) -> HeadFormat:
    """Detect raw vs validated from the columns only (never from file/folder names)."""
    if all(_find_column(df, c) is not None for c in VALIDATED_COLUMNS):
        return HeadFormat.VALIDATED
    return HeadFormat.RAW


def _first_numeric_column(df: pd.DataFrame, exclude: set[str]) -> str:
    for col in df.columns:
        if col in exclude:
            continue
        if pd.to_numeric(df[col], errors="coerce").notna().any():
            return col
    raise ValueError("No numeric value column found in head file")


def read_head_csv(
    path: str | Path,
    head_column: str = "head",
    apply_validation_filter: bool = True,
) -> LoadedHead:
    """Load one head CSV, detecting raw vs validated from its columns.

    raw        timestamp column + one value column. The value column is
               ``head_column`` if present, otherwise the first numeric column.
    validated  ``raw_data`` / ``gecontroleerd`` / ``afgekeurd`` all present.
               Rows are kept when ``gecontroleerd == 1 and afgekeurd != 1``.
               Rejected rows are dropped, never NaN-filled or interpolated.

    The returned series is sorted, float, NaN-free, and has a DatetimeIndex.
    """
    df = _read_csv(path)
    if df.empty:
        raise ValueError(f"Head file has no data rows: {path}")

    fmt = detect_format(df)
    time_col = _time_column(df)
    n_rows_read = len(df)
    n_rejected = 0

    if fmt is HeadFormat.VALIDATED:
        value_col = _find_column(df, "raw_data")
        checked_col = _find_column(df, "gecontroleerd")
        rejected_col = _find_column(df, "afgekeurd")
        values = pd.to_numeric(df[value_col], errors="coerce")
        if apply_validation_filter:
            checked = pd.to_numeric(df[checked_col], errors="coerce")
            rejected = pd.to_numeric(df[rejected_col], errors="coerce")
            keep = (checked == 1) & (rejected != 1)
            n_rejected = int((~keep).sum())
        else:
            keep = pd.Series(True, index=df.index)
    else:
        value_col = _find_column(df, head_column)
        if value_col is None or value_col == time_col:
            value_col = _first_numeric_column(df, exclude={time_col})
        values = pd.to_numeric(df[value_col], errors="coerce")
        keep = pd.Series(True, index=df.index)

    index = pd.to_datetime(df[time_col], errors="coerce")
    series = pd.Series(values.to_numpy(dtype=float), index=pd.DatetimeIndex(index))
    series = series[keep.to_numpy()]

    # Unparseable timestamps / values are not data; drop them (no interpolation).
    series = series[series.index.notna()].dropna().sort_index()
    series.name = "head"
    return LoadedHead(
        series=series, format=fmt, n_rows_read=n_rows_read, n_rejected=n_rejected
    )


def load_forcing(path: str | Path, column: str) -> pd.Series:
    """Load a daily forcing series (precipitation or evaporation, in mm).

    ``column`` is a pipeline parameter because column names differ across files
    (``precipitation_mm``, ``makkink_mm``, ``evaporation`` ...). Values are passed
    through unchanged in mm, as in the original repo.
    """
    df = _read_csv(path)
    col = _find_column(df, column)
    if col is None:
        raise KeyError(
            f"Column {column!r} not found in {path}; available columns: {list(df.columns)}"
        )
    time_col = _time_column(df)
    index = pd.DatetimeIndex(pd.to_datetime(df[time_col], errors="coerce"))
    series = pd.Series(pd.to_numeric(df[col], errors="coerce").to_numpy(dtype=float), index=index)
    series = series[series.index.notna()].sort_index()
    series = series[~series.index.duplicated(keep="first")]
    series.name = col
    return series
