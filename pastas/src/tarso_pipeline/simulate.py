"""Driving a calibrated model with new forcing (handoff section 4).

The `.pas` file is loaded, the two stresses inside its Tarso or Recharge
stressmodel (``find_forcing_key``; every model type of ``tarso_pipeline.fit`` has
one) are **replaced** with an ensemble member's series, and ``simulate()`` is called over
the member's period. Fitted parameters are untouched: this is simulation, not
re-calibration and not extrapolation. A non-linear recharge model was fitted on
mm/day, so its members are scaled from m/day the same way (``forcing_scale``). A
noise model in the `.pas` plays no part: ``simulate()`` does not use it.

The stress is replaced by assigning ``series_original`` rather than adding a new
stressmodel, so Pastas keeps its own settings (fill method, sample rate) for that
stress.

A Tarso stressmodel takes exactly two stresses, precipitation and evaporation,
and describes the head with its own internal threshold structure. It takes no
soil-moisture stress -- the ``use_soil`` keyword exists on the builder only so
every model type shares a signature, and is ignored.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

WARMUP_YEARS = 5
DAYS_PER_YEAR = 365


@dataclass(frozen=True)
class SimulationResult:
    head: pd.Series
    member: str
    tmin: pd.Timestamp
    tmax: pd.Timestamp
    warmup_days: int


def find_tarso_key(model) -> str:
    """The Tarso stressmodel's key: the only one whose name contains 'tarso'."""
    keys = [k for k in model.stressmodels if "tarso" in str(k).lower()]
    if not keys:
        raise ValueError(
            f"No Tarso stressmodel in this model; found {list(model.stressmodels)}. "
            "This stage is Tarso-only."
        )
    if len(keys) > 1:
        raise ValueError(f"Expected one Tarso stressmodel, found {keys}")
    return keys[0]


def find_forcing_key(model) -> str:
    """The stressmodel that takes precipitation and evaporation: Tarso or Recharge.

    Every model type of ``tarso_pipeline.fit`` has exactly one; a model with more than
    one two-stress stressmodel is ambiguous and refused.
    """
    keys = [k for k, sm in model.stressmodels.items()
            if getattr(sm, "_name", None) in ("TarsoModel", "RechargeModel")]
    if len(keys) != 1:
        raise ValueError(
            f"Expected one Tarso or Recharge stressmodel, found {keys or list(model.stressmodels)}"
        )
    return keys[0]


# Recharge models whose parameters assume mm/day (tarso_pipeline.fit.NONLINEAR_RECHARGE).
_MM_RECHARGE = ("Berendrecht", "Peterson", "FlexModel")


def forcing_scale(stressmodel) -> float:
    """1000 for a non-linear recharge model (fitted on mm/day), else 1 (the run's m/day)."""
    recharge = getattr(stressmodel, "recharge", None)
    return 1000.0 if type(recharge).__name__ in _MM_RECHARGE else 1.0


def _median_step(series: pd.Series) -> pd.Timedelta | None:
    if series is None or len(series) < 3:
        return None
    step = pd.Series(pd.DatetimeIndex(series.index)).diff().median()
    return None if pd.isna(step) else step


def match_timestep(new: pd.Series, existing: pd.Series) -> pd.Series:
    """Upsample incoming forcing to the model's native step, preserving magnitude.

    Models may be calibrated on hourly head with daily forcing; IMPREX is daily.
    When the incoming series is much *coarser* than the model's own step (ratio
    >= 2 and near-integer), it is forward-filled onto the finer step and divided
    by the ratio, so the per-time-unit magnitude is preserved rather than
    multiplied. For a daily model on daily IMPREX this is a no-op.
    """
    new_step, old_step = _median_step(new), _median_step(existing)
    if new_step is None or old_step is None or old_step <= pd.Timedelta(0):
        return new

    ratio = new_step / old_step
    if ratio < 2 or abs(ratio - round(ratio)) > 0.05:
        return new

    factor = int(round(ratio))
    resampled = new.resample(old_step).ffill() / factor
    resampled.name = new.name
    return resampled


HOLD_MODES = ("mean", "climatology")


def hold_other_stresses(model, forcing_key: str, index: pd.DatetimeIndex, hold: str = "mean",
                        skip: tuple = ()) -> list[str]:
    """Set every stress outside the P/E stress model to its calibration level over ``index``.

    ``mean``: one constant, the mean over the calibration period, so the stress only
    contributed its impulses (its deviations from that mean) and adds a constant here.
    ``climatology``: its mean per day of the year over the calibration period, for a
    sensitivity run. Returns the names of the stress models it held.
    """
    if hold not in HOLD_MODES:
        raise ValueError(f"hold must be one of {HOLD_MODES}, got {hold!r}")
    tmin, tmax = model.settings.get("tmin"), model.settings.get("tmax")
    held = []
    for name, stressmodel in model.stressmodels.items():
        if name == forcing_key or name in skip:
            continue
        for stress in stressmodel.stress:
            original = stress.series_original
            window = original.loc[tmin:tmax] if tmin is not None else original
            window = window if window.count() else original
            if hold == "mean":
                values = pd.Series(float(window.mean()), index=index)
            else:
                by_day = window.groupby(window.index.day_of_year).mean()
                by_day = by_day.reindex(range(1, 367)).interpolate(limit_direction="both")
                values = pd.Series(by_day.loc[index.day_of_year].to_numpy(), index=index)
            values.name = original.name
            stress.series_original = values
        held.append(name)
    return held


def simulate_member(
    model,
    precipitation: pd.Series,
    evaporation: pd.Series,
    member: str = "",
    warmup_years: int = WARMUP_YEARS,
    freq: str = "D",
    hold: str = "mean",
    synthetic: dict | None = None,
) -> SimulationResult:
    """Replace the stresses and simulate, dropping the spin-up from the front.

    Any stress model beside the P/E one (a water level, air pressure) has no IMPREX
    counterpart; ``hold_other_stresses`` keeps it at its calibration mean (``hold``).
    ``synthetic`` maps a stress model name to a synthetic series for this member, on the
    same reference as the series the model was fitted on (e.g. an emulated boezem level
    minus the same calibration mean); those stress models are replaced, not held.

    The members start on 1951-01-01 with no history and the Tarso response
    carries a memory of years, so the first ``warmup_years`` are computed but
    never read: ``warmup`` covers them and ``tmin`` starts after them.
    """
    key = find_forcing_key(model)
    stressmodel = model.stressmodels[key]
    if len(stressmodel.stress) < 2:
        raise ValueError(
            f"{key} stressmodel has {len(stressmodel.stress)} stress(es); expected 2"
        )

    scale = forcing_scale(stressmodel)
    if scale != 1.0:
        precipitation, evaporation = precipitation * scale, evaporation * scale
    precipitation = match_timestep(precipitation, stressmodel.stress[0].series_original)
    evaporation = match_timestep(evaporation, stressmodel.stress[1].series_original)

    stressmodel.stress[0].series_original = precipitation
    stressmodel.stress[1].series_original = evaporation
    synthetic = synthetic or {}
    unknown = set(synthetic) - set(model.stressmodels)
    if unknown:
        raise ValueError(f"synthetic stresses for unknown stress models: {sorted(unknown)}")
    for name, series in synthetic.items():
        stress = model.stressmodels[name].stress[0]
        values = series.reindex(precipitation.index)
        values.name = stress.series_original.name
        stress.series_original = values
    hold_other_stresses(model, key, precipitation.index, hold=hold, skip=tuple(synthetic))

    warmup_days = warmup_years * DAYS_PER_YEAR
    tmin = pd.Timestamp(precipitation.index[0]) + pd.DateOffset(years=warmup_years)
    tmax = min(pd.Timestamp(precipitation.index[-1]), pd.Timestamp(evaporation.index[-1]))
    if tmin >= tmax:
        raise ValueError(
            f"warmup of {warmup_years} years leaves nothing to simulate between "
            f"{precipitation.index[0].date()} and {tmax.date()}"
        )

    head = model.simulate(tmin=tmin, tmax=tmax, freq=freq, warmup=warmup_days)
    head = head.dropna()
    head.name = "head"

    return SimulationResult(
        head=head, member=member, tmin=tmin, tmax=tmax, warmup_days=warmup_days
    )
