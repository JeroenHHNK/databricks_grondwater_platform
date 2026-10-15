"""Driving a calibrated Tarso model with new forcing (handoff section 4).

The `.pas` file is loaded, the two stresses inside its Tarso stressmodel are
**replaced** with an ensemble member's series, and ``simulate()`` is called over
the member's period. Fitted parameters are untouched: this is simulation, not
re-calibration and not extrapolation.

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


def simulate_member(
    model,
    precipitation: pd.Series,
    evaporation: pd.Series,
    member: str = "",
    warmup_years: int = WARMUP_YEARS,
    freq: str = "D",
) -> SimulationResult:
    """Replace the stresses and simulate, dropping the spin-up from the front.

    The members start on 1951-01-01 with no history and the Tarso response
    carries a memory of years, so the first ``warmup_years`` are computed but
    never read: ``warmup`` covers them and ``tmin`` starts after them.
    """
    key = find_tarso_key(model)
    stressmodel = model.stressmodels[key]
    if len(stressmodel.stress) < 2:
        raise ValueError(
            f"Tarso stressmodel has {len(stressmodel.stress)} stress(es); expected 2"
        )

    precipitation = match_timestep(precipitation, stressmodel.stress[0].series_original)
    evaporation = match_timestep(evaporation, stressmodel.stress[1].series_original)

    stressmodel.stress[0].series_original = precipitation
    stressmodel.stress[1].series_original = evaporation

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
