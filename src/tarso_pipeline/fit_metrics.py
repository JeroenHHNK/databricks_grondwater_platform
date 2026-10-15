"""The `pastas_fit_metrics` row (handover section 5).

``ml.fit_report()`` is a human-formatted string and its layout is not a stable
API, so nothing here parses it. Every value is read from the API instead, and
each read is individually guarded: the stats API has shifted across Pastas
releases, so a field the installed version does not expose lands as ``None``
rather than losing the whole row.

Verified against pastas 1.14: every field below resolves.
"""

from __future__ import annotations

import datetime as dt
import json
from typing import Any, Callable

import pandas as pd

# Column -> Spark type, in order. The notebook builds its DataFrame from this, so
# the table schema and the row cannot drift apart.
FIT_METRICS_SCHEMA: dict[str, str] = {
    "run_id": "string",
    "series_id": "string",
    "series_name": "string",
    "model_type": "string",
    "success": "boolean",
    "error_message": "string",
    # solver / observations
    "nfev": "int",
    "nobs": "int",
    "noise": "boolean",
    "tmin": "timestamp",
    "tmax": "timestamp",
    "freq": "string",
    "freq_obs": "string",
    "warmup_days": "int",
    "warmup_available_days": "int",
    "calibration_trimmed": "boolean",
    "days_trimmed_from_end": "int",
    # fit statistics
    "evp": "double",
    "r2": "double",
    "rmse": "double",
    "aic": "double",
    "aicc": "double",
    "bic": "double",
    "obj": "double",
    "interp": "boolean",
    # what was actually fitted
    "n_raw_rows": "int",
    "n_missing_value": "int",
    "n_duplicate_timestamps": "int",
    "n_after_dedup": "int",
    "dedup_method": "string",
    "resample_rule": "string",
    "resample_method": "string",
    "span_days": "int",
    "first_entry": "timestamp",
    "last_entry": "timestamp",
    "min_value": "double",
    "max_value": "double",
    # provenance
    "parameters": "string",
    "model_path": "string",
    "plot_path": "string",
    "diagnostics_path": "string",
    "forcing_station": "string",
    "forcing_units": "string",
    "pastas_version": "string",
    "fitted_at": "timestamp",
}


def _safe(getter: Callable[[], Any], cast: Callable[[Any], Any] | None = None):
    """Read one field, returning None if this Pastas version does not expose it."""
    try:
        value = getter()
    except Exception:
        return None
    if value is None:
        return None
    try:
        return cast(value) if cast else value
    except Exception:
        return None


def _timedelta_days(value) -> int | None:
    try:
        return int(pd.Timedelta(value).days)
    except Exception:
        return None


def make_run_id(now: dt.datetime | None = None) -> str:
    """``yy_mm_dd_HH_MM``, as the original repo tags its runs.

    One per run, so appended metrics rows stay grouped and comparable.
    """
    return (now or dt.datetime.now()).strftime("%y_%m_%d_%H_%M")


def empty_row() -> dict:
    """Every column present and null -- the base every row is built on."""
    return {name: None for name in FIT_METRICS_SCHEMA}


def extract_metrics(model, model_type: str = "tarso") -> dict:
    """Pull the fit report's contents out of a solved model, via the API."""
    import pastas as ps

    settings = getattr(model, "settings", {}) or {}
    row = {
        "model_type": model_type,
        "success": True,
        "error_message": None,
        "nfev": _safe(lambda: model.solver.nfev, int),
        "nobs": _safe(lambda: model.oseries.series.size, int),
        "noise": _safe(lambda: bool(settings.get("noise"))),
        "tmin": _safe(lambda: pd.Timestamp(settings["tmin"])),
        "tmax": _safe(lambda: pd.Timestamp(settings["tmax"])),
        "freq": _safe(lambda: str(settings["freq"])),
        "freq_obs": _safe(
            lambda: None if settings.get("freq_obs") is None else str(settings["freq_obs"])
        ),
        "warmup_days": _safe(lambda: settings["warmup"], _timedelta_days),
        "evp": _safe(model.stats.evp, float),
        "r2": _safe(model.stats.rsq, float),
        "rmse": _safe(model.stats.rmse, float),
        "aic": _safe(model.stats.aic, float),
        "aicc": _safe(model.stats.aicc, float),
        "bic": _safe(model.stats.bic, float),
        "obj": _safe(lambda: model.solver.obj_func, float),
        # None means "simulation not interpolated onto the observations", which
        # is what the report prints as "Interp. No".
        "interp": bool(_safe(lambda: model.interpolate_simulation) or False),
        "parameters": _safe(
            lambda: json.dumps(
                {k: (None if pd.isna(v) else float(v))
                 for k, v in model.parameters["optimal"].items()},
                sort_keys=True,
            )
        ),
        "pastas_version": ps.__version__,
        "fitted_at": dt.datetime.now(),
    }
    return row


def observed_fields(series: pd.Series) -> dict:
    """Extent of the series that was actually fitted."""
    if series is None or series.empty:
        return {
            "span_days": 0,
            "first_entry": None,
            "last_entry": None,
            "min_value": None,
            "max_value": None,
        }
    return {
        "span_days": int((series.index.max() - series.index.min()).days),
        "first_entry": pd.Timestamp(series.index.min()),
        "last_entry": pd.Timestamp(series.index.max()),
        "min_value": float(series.min()),
        "max_value": float(series.max()),
    }


def error_row(
    run_id: str,
    series_id: str,
    series_name: str,
    model_type: str,
    message: str,
) -> dict:
    """A failed series is a row, not a crash (handover section 5)."""
    import pastas as ps

    row = empty_row()
    row.update(
        {
            "run_id": run_id,
            "series_id": series_id,
            "series_name": series_name,
            "model_type": model_type,
            "success": False,
            "error_message": message,
            "pastas_version": ps.__version__,
            "fitted_at": dt.datetime.now(),
        }
    )
    return row


def rows_to_frame(rows: list[dict]) -> pd.DataFrame:
    """Stack rows with the columns pinned to ``FIT_METRICS_SCHEMA`` order."""
    return pd.DataFrame(rows, columns=list(FIT_METRICS_SCHEMA))


def spark_schema() -> str:
    """``FIT_METRICS_SCHEMA`` as a Spark DDL string."""
    return ", ".join(f"{name} {dtype}" for name, dtype in FIT_METRICS_SCHEMA.items())
