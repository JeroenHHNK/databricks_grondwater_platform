"""Per-series exceptions to the shared settings: ``pastas/config/series_overrides.csv``.

The settings (``config/model_settings.toml``) hold for every series. Some locations know
something the data cannot show: a pumping well next to the filter, a lake stress that
cannot reach a deep aquifer behind a sheet pile, a radar pixel over the wrong land use.
Such a fact goes in this file, one row per series, with a note on why and who added it,
so it is in git and visible in the selection's reason.

Columns
-------
``meetreeks_id``          the series (the key; ``series_name`` is only for people).
``series_name``           e.g. ``series_c2d4ccd19f6dd8e691f242fc``.
``exclude_stresses``      ``;``-separated, of ``lake``, ``pressure``, ``bucket``, ``radar``:
                          the model types with that stress (or radar precipitation) are not
                          fitted for this series and never picked.
``exclude_model_types``   ``;``-separated model types (``tarso_pipeline.fit.MODEL_TYPES``),
                          not fitted and never picked.
``trust_extremes``        ``nee`` (or false/0): the return periods of this series are
                          computed as usual but the pick's reason says not to trust them,
                          with the note. Empty = ``ja``.
``note``                  why; ``added_by``, ``added_at``: who and when.

Only the rows that name a series do anything; an empty or missing file changes nothing.
This is not the model *choice* (``model_choice/``, a person picking one model in the
app): an override narrows what the automatic selection may pick from.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from tarso_pipeline.settings import CONFIG_DIR

OVERRIDES_FILE = CONFIG_DIR / "series_overrides.csv"
OVERRIDE_COLUMNS = ["meetreeks_id", "series_name", "exclude_stresses", "exclude_model_types", "trust_extremes",
                    "note", "added_by", "added_at"]
EXCLUDABLE = ("lake", "pressure", "bucket", "radar")
_NO = {"nee", "no", "false", "0", "n"}


def _tokens(value) -> list[str]:
    return [t.strip() for t in str(value or "").replace(",", ";").split(";") if t.strip()]


def read_overrides(path: str | Path | None = None) -> pd.DataFrame:
    """The overrides, one row per series, checked; an empty frame without a file."""
    from tarso_pipeline.fit import MODEL_TYPES

    path = Path(path) if path else OVERRIDES_FILE
    if not path.is_file():
        return pd.DataFrame(columns=OVERRIDE_COLUMNS)
    frame = pd.read_csv(path, dtype=str, keep_default_na=False, encoding="utf-8-sig")
    for column in OVERRIDE_COLUMNS:
        if column not in frame:
            frame[column] = ""
    frame = frame[frame["meetreeks_id"].str.strip() != ""][OVERRIDE_COLUMNS].copy()
    frame["meetreeks_id"] = frame["meetreeks_id"].str.strip()
    duplicated = frame["meetreeks_id"][frame["meetreeks_id"].duplicated()].unique()
    if len(duplicated):
        raise ValueError(f"{path.name}: more than one row for {list(duplicated)}; one row per series")
    for row in frame.itertuples():
        bad = [t for t in _tokens(row.exclude_stresses) if t not in EXCLUDABLE]
        if bad:
            raise ValueError(f"{path.name}, {row.meetreeks_id}: exclude_stresses {bad}; use {list(EXCLUDABLE)}")
        bad = [t for t in _tokens(row.exclude_model_types) if t not in MODEL_TYPES]
        if bad:
            raise ValueError(f"{path.name}, {row.meetreeks_id}: unknown exclude_model_types {bad}")
    return frame.reset_index(drop=True)


def _row(overrides: pd.DataFrame, meetreeks_id: str):
    if overrides is None or overrides.empty:
        return None
    rows = overrides[overrides["meetreeks_id"] == meetreeks_id]
    return None if rows.empty else rows.iloc[0]


def excludes(overrides: pd.DataFrame, meetreeks_id: str, model_type: str) -> bool:
    """True when the overrides rule ``model_type`` out for this series."""
    from tarso_pipeline.fit import model_spec

    row = _row(overrides, meetreeks_id)
    if row is None:
        return False
    if model_type in _tokens(row["exclude_model_types"]):
        return True
    stresses = set(_tokens(row["exclude_stresses"]))
    if not stresses:
        return False
    try:
        spec = model_spec(model_type)
    except (KeyError, ValueError):
        return False
    return bool(stresses & set(spec.stresses)) or ("radar" in stresses and spec.radar)


def extremes_note(overrides: pd.DataFrame, meetreeks_id: str) -> str:
    """``"extremen niet betrouwbaar: <note>"`` when ``trust_extremes`` is no; else empty."""
    row = _row(overrides, meetreeks_id)
    if row is None or str(row["trust_extremes"]).strip().lower() not in _NO:
        return ""
    note = str(row["note"]).strip()
    return "extremen niet betrouwbaar" + (f": {note}" if note else "")
