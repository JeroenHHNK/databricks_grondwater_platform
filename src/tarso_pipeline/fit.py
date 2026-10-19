"""Fit one series from the `meting` table end to end (handover sections 3-5).

``fit_series`` is the unit of work: load rows -> clean -> fit -> save -> one
metrics row. It **never raises**. A failure becomes a row with ``success=False``
and the message, because one malformed series must not abort a run over twenty.

Two model types:

``tarso``     ``ps.TarsoModel`` -- the target, with per-series drainage levels.
``recharge``  ``ps.RechargeModel`` with an Exponential response -- the plain
              Pastas baseline the handover asks for as a comparison. Cheap, and
              it tells you whether a poor Tarso fit is the model or the data.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from tarso_pipeline.clean import CleanedSeries, clean_head
from tarso_pipeline.forcing import check_coverage
from tarso_pipeline.fit_metrics import (
    empty_row,
    error_row,
    extract_metrics,
    observed_fields,
)

MODEL_TYPES = ("tarso", "recharge")
STRESSMODEL_NAME = "Tarso"
RECHARGE_NAME = "Recharge"


def build_model(
    model_type: str,
    series_name: str,
    head: pd.Series,
    precipitation: pd.Series,
    evaporation: pd.Series,
):
    """Construct an unsolved model of the requested type."""
    import pastas as ps

    if model_type == "tarso":
        # constant=False: TarsoModel brings its own drainage levels, so a Pastas
        # constant would be redundant and unidentifiable. dmin/dmax are this
        # series' own observed range, not global constants.
        model = ps.Model(head, name=series_name, constant=False)
        model.add_stressmodel(
            ps.TarsoModel(
                precipitation, evaporation,
                dmin=head.min(), dmax=head.max(), name=STRESSMODEL_NAME,
            )
        )
        # Drainage resistances in days; without bounds the solver wanders
        # somewhere physically meaningless.
        model.set_parameter("Tarso_a0", pmin=0.0, pmax=200.0)
        model.set_parameter("Tarso_a1", pmin=10.0, pmax=200.0)
        return model

    if model_type == "recharge":
        model = ps.Model(head, name=series_name)
        model.add_stressmodel(
            ps.RechargeModel(
                precipitation, evaporation,
                rfunc=ps.Exponential(), name=RECHARGE_NAME,
            )
        )
        return model

    raise ValueError(f"Unknown model_type {model_type!r}; use one of {MODEL_TYPES}")


def _save_plot(model, kind: str, path: Path) -> str | None:
    """Save ``ml.plots.<kind>()`` as a PNG. Returns the path, or None on failure.

    A plot is a nice-to-have: a model that fitted but could not be drawn is still
    a fitted model, so this never propagates. Figures are closed explicitly --
    pyplot keeps every one alive otherwise, and 20 series x 2 plots would exhaust
    memory.
    """
    import matplotlib

    matplotlib.use("Agg")  # no display on a cluster
    import matplotlib.pyplot as plt

    figure = None
    try:
        axes = getattr(model.plots, kind)(figsize=(10, 6))
        # These return axes, a list of them, or occasionally a figure.
        if hasattr(axes, "savefig"):
            figure = axes
        else:
            first = axes[0] if isinstance(axes, (list, tuple)) else axes
            figure = first.get_figure()
        path.parent.mkdir(parents=True, exist_ok=True)
        figure.savefig(str(path), dpi=150, bbox_inches="tight")
        return str(path)
    except Exception:
        return None
    finally:
        if figure is not None:
            plt.close(figure)
        plt.close("all")


def fit_series(
    meetreeks_id: str,
    series_name: str,
    raw: pd.DataFrame,
    precipitation: pd.Series,
    evaporation: pd.Series,
    run_id: str,
    model_type: str = "tarso",
    run_directory: str | None = None,
    time_column: str = "tijdstempel",
    value_column: str = "waarde",
    dedup_method: str = "mean",
    resample_rule: str = "D",
    resample_method: str = "mean",
    min_obs: int = 100,
    min_days: int = 365,
    warmup_days: int = 3650,
    forcing_station: str | None = None,
    forcing_units: str = "m",
    save_plot: bool = True,
) -> dict:
    """Clean, fit, save and report one series. Never raises."""
    row = empty_row()
    row.update(
        {
            "run_id": run_id,
            "meetreeks_id": meetreeks_id,
            "series_name": series_name,
            "model_type": model_type,
            "forcing_station": None if forcing_station is None else str(forcing_station),
            "forcing_units": forcing_units,
        }
    )

    try:
        cleaned: CleanedSeries = clean_head(
            raw,
            time_column=time_column,
            value_column=value_column,
            dedup_method=dedup_method,
            resample_rule=resample_rule,
            resample_method=resample_method,
        )
        row.update(cleaned.as_row())
        row.update(observed_fields(cleaned.series))

        # Guard before handing Pastas something it cannot fit, so the reason is
        # recorded rather than surfacing as an opaque solver error.
        if cleaned.n_final < min_obs:
            raise ValueError(
                f"only {cleaned.n_final} observations after cleaning (min_obs={min_obs})"
            )
        if cleaned.span_days < min_days:
            raise ValueError(
                f"cleaned series spans {cleaned.span_days} days (min_days={min_days})"
            )

        head = cleaned.series

        # Calibrate only where forcing exists. KNMI publishes 1-2 days behind, so
        # a series running to today always overshoots it; trimming here is
        # deliberate and recorded, rather than left for Pastas to resolve.
        coverage = check_coverage(precipitation, head, warmup_days=warmup_days)
        if not coverage.usable:
            raise ValueError(
                f"forcing {coverage.forcing_start.date()}..{coverage.forcing_end.date()} "
                f"does not overlap head {coverage.head_start.date()}.."
                f"{coverage.head_end.date()}"
            )
        row["calibration_trimmed"] = bool(coverage.trimmed)
        row["days_trimmed_from_end"] = int(coverage.days_trimmed_from_end)
        row["warmup_available_days"] = int(coverage.available_warmup_days)

        model = build_model(model_type, series_name, head, precipitation, evaporation)
        model.solve(
            tmin=coverage.tmin.strftime("%Y-%m-%d"),
            tmax=coverage.tmax.strftime("%Y-%m-%d"),
            report=False,
        )

        metrics = extract_metrics(model, model_type=model_type)
        row.update(metrics)
        row.update(
            {
                "run_id": run_id,
                "meetreeks_id": meetreeks_id,
                "series_name": series_name,
            }
        )
        row.update(cleaned.as_row())
        row.update(observed_fields(head))

        if run_directory:
            from tarso_pipeline.paths import (
                DIAGNOSTICS_DIR,
                PAS_DIR,
                RESULTS_DIR,
                artefact_dir,
            )

            # The model type is in the filename because one series can be fitted
            # with more than one, and the three folders are shared across series:
            # without it, a recharge run would silently overwrite the tarso one.
            stem = f"{series_name}_{model_type}"

            pas_path = Path(artefact_dir(run_directory, PAS_DIR)) / f"{stem}.pas"
            pas_path.parent.mkdir(parents=True, exist_ok=True)
            model.to_file(str(pas_path))
            row["model_path"] = str(pas_path)

            if save_plot:
                row["plot_path"] = _save_plot(
                    model,
                    "results",
                    Path(artefact_dir(run_directory, RESULTS_DIR)) / f"{stem}_results.png",
                )
                row["diagnostics_path"] = _save_plot(
                    model,
                    "diagnostics",
                    Path(artefact_dir(run_directory, DIAGNOSTICS_DIR))
                    / f"{stem}_diagnostics.png",
                )

        row["success"] = True
        row["error_message"] = None
        return row

    except Exception as exc:  # noqa: BLE001 - a failed series is data, not a crash
        failure = error_row(
            run_id=run_id,
            meetreeks_id=meetreeks_id,
            series_name=series_name,
            model_type=model_type,
            message=f"{type(exc).__name__}: {exc}",
        )
        # Keep whatever cleaning facts were established before the failure.
        for key, value in row.items():
            if value is not None and failure.get(key) is None:
                failure[key] = value
        return failure
