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
``flex_<rfunc>``       a soil-moisture bucket (``ps.rch.FlexModel`` on mm/day, ``kv`` fixed at 1).
``<type>_lake_baro``   + the Markermeer level at Edam and air pressure, each an instantaneous
                       response (``ps.One``) on its anomaly from the calibration mean; on the
                       linear and bucket types (Tarso allows nothing beside it).
``<type>_lake_baro_bucket``  + also the deficit of a root-zone soil-moisture bucket (Smax
                       150 mm, ET reduced below 70 % full; ``inputs.bucket_deficit``) built
                       from the model's own P and E, with an Exponential response whose gain
                       may take either sign. It acts only when the soil dries out, so it adds
                       the extra drop (or holds back the linear one) in a drought. Solved
                       nested (``solve_model``) on the same type without it.
``<type>_radar``       the same model on the polder's KNMI radar precipitation instead of
                       the station's (``tarso_pipeline.inputs``).

The extended set (``EXTENDED_MODEL_TYPES``) adds the lake + pressure types, with and
without the bucket, on station and radar precipitation. On the validated Purmer/Beemster
series lake + pressure took the passed models from 35 to 61
(``docs/experiments/2026-11-24_gemaal-peilen.md``) and the bucket from 60 to 65, none lost
(``docs/experiments/2026-11-24_drought.md``). Every fit row records
``precipitation_source``, ``evaporation_source`` and ``extra_stresses``.

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
PROVENANCE = ("forcing_station", "forcing_units", "precipitation_source", "evaporation_source", "extra_stresses")


def _station_source(station) -> str | None:
    return None if station is None else f"knmi_{station}"
RECHARGE_NAME = "Recharge"


@dataclass(frozen=True)
class ModelSpec:
    recharge: str         # "tarso", "linear", "peterson", "flex", ...
    rfunc: str            # "exp", "gamma", "dexp", "fourparam"
    forcing_scale: float  # P and E (m/day) are multiplied by this before they go in
    stresses: tuple = ()  # extra stresses: "lake", "pressure", "bucket" (tarso_pipeline.inputs)
    radar: bool = False   # precipitation from the polder's radar pixel instead of the station


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
LAKE_BARO = ("lake", "pressure")
LAKE_BARO_BUCKET = (*LAKE_BARO, "bucket")
BUCKET = "bucket"
LINEAR_RFUNCS = ("exp", "gamma", "dexp", "fourparam")
FLEX_RFUNCS = ("exp", "gamma")
for _rfunc in LINEAR_RFUNCS:
    for _suffix, _stresses in (("_lake_baro", LAKE_BARO), ("_lake_baro_bucket", LAKE_BARO_BUCKET)):
        MODEL_SPECS[f"linear_{_rfunc}{_suffix}"] = ModelSpec("linear", _rfunc, 1.0, _stresses)
        MODEL_SPECS[f"linear_{_rfunc}{_suffix}_radar"] = ModelSpec("linear", _rfunc, 1.0, _stresses, radar=True)
for _rfunc in FLEX_RFUNCS:
    MODEL_SPECS[f"flex_{_rfunc}"] = ModelSpec("flex", _rfunc, 1000.0)
    for _suffix, _stresses in (("_lake_baro", LAKE_BARO), ("_lake_baro_bucket", LAKE_BARO_BUCKET)):
        MODEL_SPECS[f"flex_{_rfunc}{_suffix}"] = ModelSpec("flex", _rfunc, 1000.0, _stresses)
        MODEL_SPECS[f"flex_{_rfunc}{_suffix}_radar"] = ModelSpec("flex", _rfunc, 1000.0, _stresses, radar=True)
MODEL_TYPES = tuple(MODEL_SPECS)
DEFAULT_MODEL_TYPES = ("tarso", "linear_exp", "linear_gamma", "linear_dexp", "linear_fourparam")


def _family(suffix: str) -> tuple:
    return (*(f"linear_{r}{suffix}" for r in LINEAR_RFUNCS), *(f"flex_{r}{suffix}" for r in FLEX_RFUNCS))


# The default five plus lake + pressure, with and without the bucket, on station and on
# radar precipitation, so the selection can pick per series whichever passes (the source
# is logged on every row).
EXTENDED_MODEL_TYPES = (
    *DEFAULT_MODEL_TYPES,
    *_family("_lake_baro"),
    *_family("_lake_baro_radar"),
    *_family("_lake_baro_bucket"),
    *_family("_lake_baro_bucket_radar"),
)


def candidate_model_types() -> tuple:
    """The model types every series is fitted with: ``[fit] model_types`` of
    ``config/model_settings.toml``, or ``EXTENDED_MODEL_TYPES`` when it is empty."""
    from tarso_pipeline.settings import load_settings

    chosen = tuple(load_settings().fit.model_types) or EXTENDED_MODEL_TYPES
    unknown = [m for m in chosen if m not in MODEL_SPECS]
    if unknown:
        raise ValueError(f"config/model_settings.toml [fit] model_types: unknown {unknown}; use {MODEL_TYPES}")
    return chosen


CANDIDATE_MODEL_TYPES = candidate_model_types()
MODEL_LABELS = {
    "tarso": "Tarso",
    "linear_exp": "Lineair + exponentieel",
    "linear_gamma": "Lineair + gamma",
    "linear_dexp": "Lineair + dubbel exponentieel",
    "linear_fourparam": "Lineair + FourParam",
    "peterson_gamma": "Peterson + gamma",
    "recharge": "Lineair + exponentieel (oud)",
    "flex_exp": "Bakje + exponentieel",
    "flex_gamma": "Bakje + gamma",
}
for _type, _spec in list(MODEL_SPECS.items()):
    if _type not in MODEL_LABELS:
        _base = _type.replace("_lake_baro", "").replace("_bucket", "").replace("_radar", "")
        MODEL_LABELS[_type] = (MODEL_LABELS.get(_base, _base) + (" · meer + luchtdruk" if _spec.stresses else "")
                               + (" + bodemvocht" if BUCKET in _spec.stresses else "")
                               + (" · radar" if _spec.radar else ""))
STRESS_NAMES = {"lake": "level", "pressure": "baro", "bucket": "bucket"}  # stress model names in the .pas
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
    stresses: dict | None = None,
):
    """Construct an unsolved model of the requested type.

    ``precipitation`` and ``evaporation`` in the units of the run (m/day by default);
    the non-linear recharge models get them multiplied by ``forcing_scale``.
    ``stresses`` holds the extra stresses the type asks for (``spec.stresses``), each in
    its own units. They go in as the anomaly from their mean over ``head``'s period, so the
    model learns their excursions and, on IMPREX, holds them at that mean
    (``simulate.hold_other_stresses``). The P/E stress model is always added first: the
    t95 and gain in the metrics read the first stress model.
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
        if spec.recharge == "flex":
            # Only the root-zone storage, its outflow and gamma are fitted: the full FlexModel
            # was overparameterised on two-year records.
            model.set_parameter(f"{RECHARGE_NAME}_kv", initial=1.0, vary=False)
    for name in spec.stresses:
        series = (stresses or {}).get(name)
        if series is None:
            raise ValueError(f"model type {model_type} needs the stress {name!r}")
        window = series.loc[head.index.min():head.index.max()]
        if window.count() < 100:
            raise ValueError(f"stress {name!r} covers {window.count()} days of this series (< 100)")
        stress_name = STRESS_NAMES[name]
        if name == BUCKET:
            # The deficit as is (0 when the soil is full), so on IMPREX it is rebuilt per
            # member rather than held; either sign: the head may drop more or less than
            # the linear response says once the soil dries out.
            model.add_stressmodel(ps.StressModel(series.rename("bucket_deficit"), rfunc=ps.Exponential(),
                                                 name=stress_name, settings="evap", up=None))
            continue
        anomaly = (series - float(window.mean())).rename(f"{name}_anomaly")
        model.add_stressmodel(ps.StressModel(anomaly, rfunc=ps.One(), name=stress_name, settings="waterlevel"))
        limit = 5.0 if name == "lake" else 10.0  # m per m; m per hPa
        model.set_parameter(f"{stress_name}_d", pmin=-limit, pmax=limit)
    if kind is not None:
        model.add_noisemodel(getattr(ps, NOISE_MODELS[kind])())
    return model


def nested_type(model_type: str) -> str | None:
    """The same type without the bucket (``linear_exp_lake_baro_bucket_radar`` ->
    ``linear_exp_lake_baro_radar``); None for a type without it."""
    if BUCKET not in model_spec(model_type).stresses:
        return None
    nested = model_type.replace("_bucket", "")
    return nested if nested in MODEL_SPECS else None


def _sse(model) -> float:
    residuals = model.residuals()
    return float((residuals ** 2).sum()) if residuals is not None else float("inf")


_NESTED: dict = {}  # per process: the optimal parameters of the last nested fits
_NESTED_MAX = 64


def solve_model(model_type: str, series_name: str, head: pd.Series, precipitation: pd.Series,
                evaporation: pd.Series, tmin: str, tmax: str, noise: bool | str = False,
                stresses: dict | None = None):
    """Build and solve; a bucket type is solved *nested* on the same type without the bucket.

    The bucket gain's default start has the sign opposite to the usual effect, and a fit
    from there can end worse than the model without the term. So the type without it is
    solved first (cached per series and period, so the bucket and holdout fits of one
    series share it), its parameters start the bucket model, and the bucket gain starts at
    a small value of either sign; the lower sum of squares wins. Same data, so nothing leaks.
    """
    def key_of(nested_name: str) -> tuple:
        return (series_name, nested_name, tmin, tmax, len(head), head.index[-1], str(noise),
                float(precipitation.sum()))

    def remember(nested_name: str, solved) -> None:
        if len(_NESTED) >= _NESTED_MAX:
            _NESTED.pop(next(iter(_NESTED)))
        _NESTED[key_of(nested_name)] = solved.parameters["optimal"].copy()

    nested = nested_type(model_type)
    if nested is None:
        model = build_model(model_type, series_name, head, precipitation, evaporation, noise=noise,
                            stresses=stresses)
        model.solve(tmin=tmin, tmax=tmax, report=False)
        if model_spec(model_type).stresses == LAKE_BARO:
            remember(model_type, model)  # the start of its bucket type, if that is fitted next
        return model
    if key_of(nested) not in _NESTED:
        base = build_model(nested, series_name, head, precipitation, evaporation, noise=noise, stresses=stresses)
        base.solve(tmin=tmin, tmax=tmax, report=False)
        remember(nested, base)
    start = _NESTED[key_of(nested)]
    bucket = STRESS_NAMES[BUCKET]
    best = None
    for sign in (-1.0, 1.0):
        trial = build_model(model_type, series_name, head, precipitation, evaporation, noise=noise,
                            stresses=stresses)
        scale = float(trial.stressmodels[bucket].stress[0].series.std()) or 1.0
        for parameter, value in start.items():
            if parameter in trial.parameters.index and pd.notna(value):
                trial.set_parameter(parameter, initial=float(value))
        trial.set_parameter(f"{bucket}_A", initial=sign * 0.1 / scale)
        try:
            trial.solve(tmin=tmin, tmax=tmax, report=False)
        except Exception:  # noqa: BLE001 - the other sign may still solve
            continue
        if best is None or _sse(trial) < _sse(best):
            best = trial
    if best is None:
        raise RuntimeError(f"{model_type} did not solve from either sign of the bucket gain")
    return best


def holdout_r2(model_type: str, series_name: str, head: pd.Series, precipitation: pd.Series,
               evaporation: pd.Series, tmin: str, holdout: float, noise: bool | str = False,
               stresses: dict | None = None) -> float | None:
    """R2 on the last ``holdout`` share of the observations, of a fit on the rest.

    The simulation runs on over the held-out part with the forcing only, as it would
    for the IMPREX members. None when the held-out part is too short or flat.

    The model is built on the training part only: Tarso takes its drainage-level range
    (``dmin``/``dmax``) from the head it is given, and the full series would hand it the
    extremes of the part it is scored on.
    """
    if not 0 < holdout < 1:
        return None
    split = head.index[int(len(head) * (1 - holdout))]
    earlier = head[head.index <= split]
    later = head[head.index > split]
    if len(later) < 10:
        return None
    model = solve_model(model_type, series_name, earlier, precipitation, evaporation, tmin,
                        split.strftime("%Y-%m-%d"), noise=noise, stresses=stresses)
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
    stresses: dict | None = None,
    precipitation_source: str | None = None,
    evaporation_source: str | None = None,
) -> dict:
    """Clean, fit, save and report one series. Never raises.

    ``stresses`` are the extra stresses the model type needs. ``precipitation_source`` and
    ``evaporation_source`` (e.g. ``knmi_249``, ``radar_purmer``) are recorded on the row,
    defaulting to ``knmi_<forcing_station>`` (see ``tarso_pipeline.inputs.InputLibrary``).

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
            "precipitation_source": precipitation_source or _station_source(forcing_station),
            "evaporation_source": evaporation_source or _station_source(forcing_station),
            "extra_stresses": (",".join(MODEL_SPECS[model_type].stresses) or None)
            if model_type in MODEL_SPECS else None,
        }
    )
    provenance = {k: row[k] for k in PROVENANCE}

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

        model = solve_model(model_type, series_name, head, precipitation, evaporation,
                            coverage.tmin.strftime("%Y-%m-%d"), coverage.tmax.strftime("%Y-%m-%d"),
                            noise=noise, stresses=stresses)

        metrics = extract_metrics(model, model_type=model_type)
        row.update(metrics)
        row["noise_model"] = _noise_kind(noise) or "none"
        if holdout:
            row["holdout_share"] = float(holdout)
            try:
                row["r2_holdout"] = holdout_r2(model_type, series_name, head, precipitation, evaporation,
                                               coverage.tmin.strftime("%Y-%m-%d"), holdout, noise=noise,
                                               stresses=stresses)
            except Exception:  # noqa: BLE001 - a failed second fit must not lose the first
                row["r2_holdout"] = None
        row.update(
            {
                "run_id": run_id,
                "meetreeks_id": meetreeks_id,
                "series_name": series_name,
            }
        )
        row.update(provenance)
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
        failure.update(provenance)
        # Keep whatever cleaning facts were established before the failure.
        for key, value in row.items():
            if value is not None and failure.get(key) is None:
                failure[key] = value
        return failure
