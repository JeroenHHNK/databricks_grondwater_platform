"""The settings that hold for every series, read from ``pastas/config/model_settings.toml``.

One file instead of constants spread over ``fit``, ``inputs`` and ``selection``, so a change
(a threshold, the candidate model types, the bucket size) is visible in one place and in
git. Every value has a built-in default equal to what the pipeline used before the file
existed, so a missing file or a missing key changes nothing.

Per-series exceptions (a stress that does not apply at one location, extremes that should
not be trusted) are not here but in ``series_overrides.csv`` (``tarso_pipeline.overrides``).

``load_settings()`` reads the file once per process; ``load_settings(path)`` reads another
one (tests, a what-if run).
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field, fields, replace
from pathlib import Path

CONFIG_DIR = Path(__file__).resolve().parents[2] / "config"
SETTINGS_FILE = CONFIG_DIR / "model_settings.toml"


@dataclass(frozen=True)
class FitSettings:
    model_types: tuple = ()      # empty: tarso_pipeline.fit.EXTENDED_MODEL_TYPES
    holdout: float = 0.3         # share of the observations held out for r2_holdout
    min_days: int = 365          # minimum span after cleaning
    noise: str = "none"


@dataclass(frozen=True)
class SelectionSettings:
    aic_tolerance: float = 2.0             # a simpler model within this AIC of the lowest wins
    holdout_vs_tarso: float = 0.05         # drop: holdout more than this below Tarso's
    holdout_vs_without_bucket: float | None = None  # drop a bucket model: holdout more than this below the same model without it
    holdout_gap: float | None = None       # drop: holdout more than this below the best candidate's
    min_holdout: float | None = None       # drop: holdout below this (when a candidate reaches it)
    max_r2_loss: float | None = None       # before the holdout rules: drop R2 more than this below the best
    runaway_margin_m: float = 1.5          # T=100 this far beyond the observed range = runaway


@dataclass(frozen=True)
class BucketSettings:
    smax_mm: float = 150.0
    lp: float = 0.7


@dataclass(frozen=True)
class Settings:
    fit: FitSettings = field(default_factory=FitSettings)
    selection: SelectionSettings = field(default_factory=SelectionSettings)
    bucket: BucketSettings = field(default_factory=BucketSettings)
    source: str = "defaults"


_SECTIONS = {"fit": FitSettings, "selection": SelectionSettings, "bucket": BucketSettings}
_CACHE: dict = {}


def _section(cls, values: dict, name: str):
    known = {f.name for f in fields(cls)}
    unknown = set(values) - known
    if unknown:
        raise ValueError(f"model settings [{name}]: unknown key(s) {sorted(unknown)}; use {sorted(known)}")
    values = {k: (tuple(v) if isinstance(v, list) else v) for k, v in values.items()}
    # TOML has no null: "off" (or false) switches an optional rule off.
    # (``is False``/``== "off"``, not ``in``: 0.0 == False, and a 0 threshold is a real one)
    values = {k: (None if v is False or (isinstance(v, str) and v.lower() == "off") else v)
              for k, v in values.items()}
    return replace(cls(), **values)


def parse_settings(text: str, source: str = "text") -> Settings:
    data = tomllib.loads(text)
    unknown = set(data) - set(_SECTIONS)
    if unknown:
        raise ValueError(f"model settings: unknown section(s) {sorted(unknown)}; use {sorted(_SECTIONS)}")
    parts = {name: _section(cls, data.get(name, {}), name) for name, cls in _SECTIONS.items()}
    return Settings(**parts, source=source)


def load_settings(path: str | Path | None = None) -> Settings:
    """The settings of ``path`` (default ``config/model_settings.toml``); defaults without a file."""
    path = Path(path) if path else SETTINGS_FILE
    key = str(path)
    if key not in _CACHE:
        _CACHE[key] = (parse_settings(path.read_text(encoding="utf-8"), source=path.as_posix())
                       if path.is_file() else Settings())
    return _CACHE[key]
