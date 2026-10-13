"""Per-series Tarso training (spec sections 3-5).

Two layers, deliberately separated:

``train_tarso``  the fit itself, a faithful port of the original
                 ``create_tarso_model``. It raises on failure.
``train_one``    everything around one series -- load, prepare, fit, serialise --
                 and it **never raises**. A failure becomes a row with
                 ``status="error"`` and the message in ``error``.

That second property is the whole point (spec section 5): hundreds of series are
processed and individual fits genuinely fail -- too few observations, degenerate
records, non-converging solvers. One bad series must not abort the run, and a
series that fails must still appear in the report rather than vanish.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from tarso_pipeline.io import read_head_csv
from tarso_pipeline.preprocess import prepare_head, span_days

# Metrics pulled out of ml.stats.summary() by index label. Verified against
# pastas 1.14; a label the installed version does not produce lands as None
# rather than breaking the row.
STAT_LABELS = (
    "rmse",
    "sse",
    "mae",
    "nse",
    "evp",
    "rsq",
    "kge",
    "bic",
    "aic",
    "aicc",
)

STRESSMODEL_NAME = "Tarso"

# The report row, column -> Spark SQL type, in order. `mapInPandas` needs this
# declared up front, and a test asserts it matches what `train_one` actually
# returns so the two cannot drift apart.
OUTPUT_SCHEMA: dict[str, str] = {
    # identity and status
    "run_id": "string",
    "series": "string",
    "model": "string",
    "survey": "string",
    "status": "string",
    "error": "string",
    # observed data summary
    "first_entry": "string",
    "last_entry": "string",
    "length_series": "int",
    "min_value": "double",
    "max_value": "double",
    "span_days": "int",
    # fit statistics
    "rmse": "double",
    "sse": "double",
    "mae": "double",
    "nse": "double",
    "evp": "double",
    "rsq": "double",
    "kge": "double",
    "bic": "double",
    "aic": "double",
    "aicc": "double",
    "response_tmax_95": "double",
    # calibration and processing
    "tmin_cal": "string",
    "tmax_cal": "string",
    "resample_rule": "string",
    "resample_method": "string",
    "head_format": "string",
    "n_rejected": "int",
    # run identification
    "prec_file": "string",
    "prec_column": "string",
    "evap_file": "string",
    "evap_column": "string",
    "validation_filter": "boolean",
    "min_obs": "int",
    "min_days": "int",
    "tmin_cal_param": "string",
    "tmax_cal_param": "string",
    "pastas_version": "string",
    "model_path": "string",
}


def spark_schema() -> str:
    """``OUTPUT_SCHEMA`` as a Spark DDL string, for ``mapInPandas``."""
    return ", ".join(f"{name} {dtype}" for name, dtype in OUTPUT_SCHEMA.items())


def rows_to_frame(rows: list[dict]) -> pd.DataFrame:
    """Stack report rows with the columns in ``OUTPUT_SCHEMA`` order.

    ``mapInPandas`` matches the declared schema positionally, so the order has to
    be pinned here rather than left to dict insertion order.
    """
    return pd.DataFrame(rows, columns=list(OUTPUT_SCHEMA))


@dataclass(frozen=True)
class TrainingContext:
    """Everything shared by every series in one run.

    The forcing series live here so they are built once and broadcast, rather
    than re-read per task (spec section 5).
    """

    run_id: str
    precipitation: pd.Series
    evaporation: pd.Series
    prec_file: str
    prec_column: str
    evap_file: str
    evap_column: str
    model_dir: str | None = None
    head_column: str = "head"
    validation_filter: bool = True
    resample_rule: str = "D"
    resample_method: str = "mean"
    min_obs: int = 100
    min_days: int = 365
    tmin_cal: str | None = None
    tmax_cal: str | None = None
    pastas_version: str = field(default="")

    def __post_init__(self):
        if not self.pastas_version:
            import pastas as ps

            object.__setattr__(self, "pastas_version", ps.__version__)


def train_tarso(
    series_name: str,
    head: pd.Series,
    precipitation: pd.Series,
    evaporation: pd.Series,
    tmin_cal: str | None = None,
    tmax_cal: str | None = None,
):
    """Fit one TarsoModel. Raises on failure; ``train_one`` is what catches.

    ``dmin``/``dmax`` come from this series' own observed head range -- they are
    per-series drainage levels, not global constants. ``constant=False`` because
    TarsoModel brings its own drainage levels, so a Pastas constant would be
    redundant and unidentifiable. The ``a0``/``a1`` bounds are drainage
    resistances in days; without them the solver wanders somewhere physically
    meaningless and the fit fails or goes unstable.
    """
    import pastas as ps

    if head.first_valid_index() is None:
        raise ValueError(f"No valid head observations in {series_name}")
    head = head.loc[head.first_valid_index() : head.last_valid_index()]

    head_min, head_max = head.index.min(), head.index.max()
    tmin = max(pd.to_datetime(tmin_cal), head_min) if tmin_cal else head_min
    tmax = min(pd.to_datetime(tmax_cal), head_max) if tmax_cal else head_max

    model = ps.Model(head, name=series_name, constant=False)
    stressmodel = ps.TarsoModel(
        precipitation,
        evaporation,
        dmin=head.min(),
        dmax=head.max(),
        name=STRESSMODEL_NAME,
    )
    model.add_stressmodel(stressmodel)
    model.set_parameter("Tarso_a0", pmin=0.0, pmax=200.0)
    model.set_parameter("Tarso_a1", pmin=10.0, pmax=200.0)

    model.solve(
        tmin=tmin.strftime("%Y-%m-%d"),
        tmax=tmax.strftime("%Y-%m-%d"),
        report=False,
    )
    return model, model.stats.summary(), tmin, tmax


def _stats_row(summary: pd.DataFrame | None) -> dict:
    """Pull the metrics by index label, ``None`` for anything absent."""
    row: dict = {label: None for label in STAT_LABELS}
    if summary is None:
        return row
    for label in STAT_LABELS:
        if label in summary.index:
            value = summary.loc[label]
            # summary() is a one-column frame; be tolerant of a Series too.
            row[label] = float(value.iloc[0] if hasattr(value, "iloc") else value)
    return row


def _observed_row(series: pd.Series) -> dict:
    """First/last/extent of the observations actually used."""
    if series.empty:
        return {
            "first_entry": None,
            "last_entry": None,
            "length_series": 0,
            "min_value": None,
            "max_value": None,
            "span_days": 0,
        }
    return {
        "first_entry": series.index.min().strftime("%Y-%m-%d"),
        "last_entry": series.index.max().strftime("%Y-%m-%d"),
        "length_series": int(len(series)),
        "min_value": float(series.min()),
        "max_value": float(series.max()),
        "span_days": span_days(series),
    }


def _base_row(series_name: str, survey: str, context: TrainingContext) -> dict:
    """The identity and parameter fields, identical for every status."""
    return {
        "run_id": context.run_id,
        "series": series_name,
        "model": "tarso",
        "survey": survey,
        "resample_rule": context.resample_rule,
        "resample_method": context.resample_method,
        "prec_file": context.prec_file,
        "prec_column": context.prec_column,
        "evap_file": context.evap_file,
        "evap_column": context.evap_column,
        "validation_filter": context.validation_filter,
        "min_obs": context.min_obs,
        "min_days": context.min_days,
        "tmin_cal_param": context.tmin_cal,
        "tmax_cal_param": context.tmax_cal,
        "pastas_version": context.pastas_version,
    }


def _empty_observed() -> dict:
    return {
        "first_entry": None,
        "last_entry": None,
        "length_series": None,
        "min_value": None,
        "max_value": None,
        "span_days": None,
    }


def train_one(
    file_path: str,
    survey: str,
    series_name: str,
    context: TrainingContext,
) -> dict:
    """Train one series and return its report row. Never raises.

    The returned row always carries every column, whatever the status, so the
    rows stack into a Delta table without schema surprises.
    """
    row = _base_row(series_name, survey, context)
    row.update(_empty_observed())
    row.update(_stats_row(None))
    row.update(
        {
            "status": "error",
            "error": "",
            "response_tmax_95": None,
            "tmin_cal": None,
            "tmax_cal": None,
            "head_format": None,
            "n_rejected": None,
            "model_path": None,
        }
    )

    try:
        loaded = read_head_csv(
            file_path,
            head_column=context.head_column,
            apply_validation_filter=context.validation_filter,
        )
        row["head_format"] = loaded.format.value
        row["n_rejected"] = int(loaded.n_rejected)

        prepared = prepare_head(
            loaded,
            rule=context.resample_rule,
            method=context.resample_method,
            min_obs=context.min_obs,
            min_days=context.min_days,
        )
        row.update(_observed_row(prepared.series))

        if prepared.skipped:
            row["status"] = "skipped"
            row["error"] = prepared.skip_reason
            return row

        model, summary, tmin, tmax = train_tarso(
            series_name=series_name,
            head=prepared.series,
            precipitation=context.precipitation,
            evaporation=context.evaporation,
            tmin_cal=context.tmin_cal,
            tmax_cal=context.tmax_cal,
        )
        row.update(_stats_row(summary))
        row["tmin_cal"] = tmin.strftime("%Y-%m-%d")
        row["tmax_cal"] = tmax.strftime("%Y-%m-%d")

        try:
            row["response_tmax_95"] = float(
                model.get_response_tmax(STRESSMODEL_NAME, cutoff=0.95)
            )
        except Exception:
            # Documented as best-effort: a fitted model that cannot report its
            # response time is still a fitted model.
            row["response_tmax_95"] = None

        if context.model_dir:
            path = Path(context.model_dir) / f"{series_name}_tarso_model.pas"
            path.parent.mkdir(parents=True, exist_ok=True)
            model.to_file(str(path))
            row["model_path"] = str(path)

        row["status"] = "successful"
        row["error"] = ""
        return row

    except Exception as exc:  # noqa: BLE001 - a failed series is data, not a crash
        row["status"] = "error"
        row["error"] = f"{type(exc).__name__}: {exc}"
        return row


def make_run_id(now: dt.datetime | None = None) -> str:
    """``yy_mm_dd_HH_MM``, as the original repo tags its runs."""
    return (now or dt.datetime.now()).strftime("%y_%m_%d_%H_%M")
