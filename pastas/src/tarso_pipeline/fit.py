"""Fit one series from the `meting` table end to end (handover sections 3-5).

``fit_series`` is the unit of work: load rows -> clean -> fit -> save -> one
metrics row. It **never raises**. A failure becomes a row with ``success=False``
and the message, because one malformed series must not abort a run over twenty.

Model types (``MODEL_SPECS``), one ``.pas`` and one metrics row each per series:

``tarso``              ``ps.TarsoModel`` -- per-series drainage levels; Pastas fixes
                       its response to an Exponential and allows nothing beside it.
``linear_<rfunc>``     ``ps.RechargeModel`` with linear recharge ``R = P - f E`` and
                       the response ``exp`` (Exponential), ``gamma``, ``dexp``
                       (DoubleExponential) or ``fourparam`` (FourParam), plus a constant.
``peterson_gamma``     non-linear (Peterson) recharge with a Gamma response. Not in the
                       default set: it wins AIC but predicts the unseen part worse.
``recharge``           the old name of ``linear_exp``; still accepted.

The grid test on the validated series (Tarso, Linear, Berendrecht, Peterson, FlexModel
x four responses) is why the default set is Tarso and the four Linear models:
Berendrecht hit its parameter bounds on every series and ran away in the extremes,
FlexModel fitted worse, Peterson overfitted. Which model a series uses is decided
afterwards (``tarso_pipeline.selection``), not here.

``noise=True`` adds a noise model (``NOISE_MODELS``: AR(1) or ARMA(1,1)), so the
residuals are modelled rather than assumed white. AIC is then computed on the noise:
compare AIC only between fits with the same ``noise`` setting. Off by default: on the
validated series it did not make the runs test pass and it lowered the R2 of the
simulation (the noise model absorbs what the response should explain).

``holdout`` (e.g. 0.3) fits the same model a second time on the first 70 % of the
observations and scores its simulation on the last 30 % (``r2_holdout``): what the
model does on data it has not seen, which AIC alone does not tell.
"""

from __future__ import annotations

from dataclasses import dataclass
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
from tarso_pipeline.model_checks import run_checks

STRESSMODEL_NAME = "Tarso"
RECHARGE_NAME = "Recharge"


@dataclass(frozen=True)
class ModelSpec:
    recharge: str         # "tarso", "linear", "peterson", ...
    rfunc: str            # "exp", "gamma", "dexp", "fourparam"
    forcing_scale: float  # P and E (m/day) are multiplied by this before they go in


RFUNCS = {"exp": "Exponential", "gamma": "Gamma", "dexp": "DoubleExponential", "fourparam": "FourParam"}
# Non-linear recharge assumes P and E in mm/day (its default parameters do).
NONLINEAR_RECHARGE = {"berendrecht": "Berendrecht", "peterson": "Peterson", "flex": "FlexModel"}
MODEL_SPECS: dict[str, ModelSpec] = {
    "tarso": ModelSpec("tarso", "exp", 1.0),
    "linear_exp": ModelSpec("linear", "exp", 1.0),
    "linear_gamma": ModelSpec("linear", "gamma", 1.0),
    "linear_dexp": ModelSpec("linear", "dexp", 1.0),
    "linear_fourparam": ModelSpec("linear", "fourparam", 1.0),
    "peterson_gamma": ModelSpec("peterson", "gamma", 1000.0),
    "recharge": ModelSpec("linear", "exp", 1.0),  # the old name of linear_exp
}
MODEL_TYPES = tuple(MODEL_SPECS)
DEFAULT_MODEL_TYPES = ("tarso", "linear_exp", "linear_gamma", "linear_dexp", "linear_fourparam")
MODEL_LABELS = {
    "tarso": "Tarso",
    "linear_exp": "Lineair + exponentieel",
    "linear_gamma": "Lineair + gamma",
    "linear_dexp": "Lineair + dubbel exponentieel",
    "linear_fourparam": "Lineair + FourParam",
    "peterson_gamma": "Peterson + gamma",
    "recharge": "Lineair + exponentieel (oud)",
}
NOISE_MODELS = {"ar1": "ArNoiseModel", "arma": "ArmaNoiseModel"}


def model_spec(model_type: str) -> ModelSpec:
    if model_type not in MODEL_SPECS:
        raise ValueError(f"Unknown model_type {model_type!r}; use one of {MODEL_TYPES}")
    return MODEL_SPECS[model_type]


def _noise_kind(noise) -> str | None:
    """``False``/``None``/"" -> no noise model; ``True`` -> AR(1); or a key of NOISE_MODELS."""
    if not noise or noise == "none":
        return None
    kind = "ar1" if noise is True else str(noise)
    if kind not in NOISE_MODELS:
        raise ValueError(f"noise must be one of {('none', *NOISE_MODELS)}, got {noise!r}")
    return kind


def build_model(
    model_type: str,
    series_name: str,
    head: pd.Series,
    precipitation: pd.Series,
    evaporation: pd.Series,
    noise: bool | str = False,
):
    """Construct an unsolved model of the requested type.

    ``precipitation`` and ``evaporation`` in the units of the run (m/day by default);
    the non-linear recharge models get them multiplied by ``forcing_scale``.
    """
    import pastas as ps

    spec = model_spec(model_type)
    kind = _noise_kind(noise)
    if spec.recharge == "tarso":
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
    else:
        rfunc = getattr(ps, RFUNCS[spec.rfunc])()
        recharge = (ps.rch.Linear() if spec.recharge == "linear"
                    else getattr(ps.rch, NONLINEAR_RECHARGE[spec.recharge])())
        model = ps.Model(head, name=series_name)
        model.add_stressmodel(
            ps.RechargeModel(
                precipitation * spec.forcing_scale, evaporation * spec.forcing_scale,
                rfunc=rfunc, recharge=recharge, name=RECHARGE_NAME,
            )
        )
    if kind is not None:
        model.add_noisemodel(getattr(ps, NOISE_MODELS[kind])())
    return model


def holdout_r2(model_type: str, series_name: str, head: pd.Series, precipitation: pd.Series,
               evaporation: pd.Series, tmin: str, holdout: float, noise: bool | str = False) -> float | None:
    """R2 on the last ``holdout`` share of the observations, of a fit on the rest.

    The simulation runs on over the held-out part with the forcing only, as it would
    for the IMPREX members. None when the held-out part is too short or flat.
    """
    if not 0 < holdout < 1:
        return None
    split = head.index[int(len(head) * (1 - holdout))]
    later = head[head.index > split]
    if len(later) < 10:
        return None
    model = build_model(model_type, series_name, head, precipitation, evaporation, noise=noise)
    model.solve(tmin=tmin, tmax=split.strftime("%Y-%m-%d"), report=False)
    observed, simulated = later.align(model.simulate(tmin=head.index[0], tmax=head.index[-1]), join="inner")
    spread = float(((observed - observed.mean()) ** 2).sum())
    if len(observed) < 10 or spread == 0:
        return None
    return float(1 - ((observed - simulated) ** 2).sum() / spread)


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


SIMULATION_COLUMNS = ["run_id", "meetreeks_id", "series_name", "model_type", "datum",
                      "observed", "simulated"]


def simulation_frame(model, ids: dict) -> pd.DataFrame:
    """Daily observed and simulated head over the calibration period, one row per day.

    What the app plots for a model, so it never has to load a ``.pas``. Days without
    an observation keep the simulation and have ``observed`` NaN.
    """
    simulated = model.simulate()
    observed = model.observations()
    frame = pd.DataFrame({"simulated": simulated}).join(pd.DataFrame({"observed": observed}), how="left")
    frame = frame.rename_axis("datum").reset_index()
    for key, value in ids.items():
        frame[key] = value
    frame["datum"] = pd.to_datetime(frame["datum"]).dt.normalize()
    return frame[SIMULATION_COLUMNS]


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
    noise: bool | str = False,
    holdout: float = 0.0,
    check_rows: list | None = None,
    simulation_frames: list | None = None,
) -> dict:
    """Clean, fit, save and report one series. Never raises.

    ``noise`` adds a noise model (``True`` = AR(1), or "ar1" / "arma"); ``holdout``
    (0..1) adds ``r2_holdout``, the R2 on that last share of a second fit on the rest.

    Pass a list as ``check_rows`` to have the Brakenhoff (2022) checklist run on
    the solved model and its `pastas_model_checks` rows appended to it. A fit
    that fails adds nothing: there is no model to check. Likewise a list as
    ``simulation_frames`` collects the daily observed/simulated head
    (``simulation_frame``) of each successful fit.
    """
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

        model = build_model(model_type, series_name, head, precipitation, evaporation, noise=noise)
        model.solve(
            tmin=coverage.tmin.strftime("%Y-%m-%d"),
            tmax=coverage.tmax.strftime("%Y-%m-%d"),
            report=False,
        )

        metrics = extract_metrics(model, model_type=model_type)
        row.update(metrics)
        row["noise_model"] = _noise_kind(noise) or "none"
        if holdout:
            row["holdout_share"] = float(holdout)
            try:
                row["r2_holdout"] = holdout_r2(model_type, series_name, head, precipitation, evaporation,
                                               coverage.tmin.strftime("%Y-%m-%d"), holdout, noise=noise)
            except Exception:  # noqa: BLE001 - a failed second fit must not lose the first
                row["r2_holdout"] = None
        row.update(
            {
                "run_id": run_id,
                "meetreeks_id": meetreeks_id,
                "series_name": series_name,
            }
        )
        row.update(cleaned.as_row())
        row.update(observed_fields(head))

        if check_rows is not None:
            check_rows.extend(
                run_checks(
                    model,
                    {
                        "run_id": run_id,
                        "meetreeks_id": meetreeks_id,
                        "series_name": series_name,
                        "model_type": model_type,
                    },
                )
            )

        if simulation_frames is not None:
            try:
                simulation_frames.append(simulation_frame(model, {
                    "run_id": run_id, "meetreeks_id": meetreeks_id,
                    "series_name": series_name, "model_type": model_type,
                }))
            except Exception:  # noqa: BLE001 - a missing plot series must not lose the fit
                pass

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
