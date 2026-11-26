"""A synthetic Schermerboezem level: can P and E imitate the gauge well enough for IMPREX?

    python research/accepted/emulate_boezem.py local_runs/research/boezem_emulator

The emulator is a Pastas model of the daily boezem level (HHNK gauge at Dijkgraaf de Boer,
Beemster side) driven by the same KNMI 249 P and E as the groundwater models:

``pe``        RechargeModel(P, E), Exponential: net rain fills the boezem faster than the
              pumps drain it.
``p_e``       P (Gamma) and E (Exponential) as two stress models: rain and drying apart.
``p_e_season`` ``p_e`` plus an annual harmonic (sin, cos; instantaneous): the managed
              set point that moves with the season.
``pulse``     a short rain pulse (Exponential, a <= 10 days, gain up) plus the harmonic.
``pulse_wet`` ``pulse`` plus RechargeModel(P, E) with a *negative* gain: after wet weeks
              the water board holds the boezem lower (the observed level correlates -0.31
              with 30-day net rain and +0.30 with evaporation).

Calibrated on 2019-05 .. 2023-12, validated on 2024-01 .. now (the groundwater calibration
years) and, out of sample on another gauge, on Purmer Zuid 2015-09 .. 2019-04. The chosen
emulator is then driven by every IMPREX member (``synthetic_members``) and its annual
maxima and monthly statistics are compared with the observed ones.
"""

from __future__ import annotations

import json
import sys
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

PASTAS = Path(__file__).resolve().parents[2]
for path in (PASTAS, PASTAS / "src"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from research import data  # noqa: E402
from research.accepted import stresses  # noqa: E402

CALIBRATION = ("2019-05-01", "2023-12-31")
VALIDATION = ("2024-01-01", None)
OTHER_GAUGE = ("2015-09-22", "2019-04-30")
WARMUP_DAYS = 3650
FORMS = ("pe", "p_e", "p_e_season", "pulse", "pulse_wet")


def harmonics(index: pd.DatetimeIndex) -> tuple[pd.Series, pd.Series]:
    angle = 2 * np.pi * index.day_of_year / 365.25
    return pd.Series(np.sin(angle), index, name="season_sin"), pd.Series(np.cos(angle), index, name="season_cos")


def build(form: str, level: pd.Series, precipitation: pd.Series, evaporation: pd.Series):
    import pastas as ps

    model = ps.Model(level, name=f"boezem_{form}")
    if form == "pe":
        model.add_stressmodel(ps.RechargeModel(precipitation, evaporation, rfunc=ps.Exponential(), name="Recharge"))
    elif form in ("p_e", "p_e_season"):
        model.add_stressmodel(ps.StressModel(precipitation, rfunc=ps.Gamma(), name="rain", settings="prec"))
        model.add_stressmodel(ps.StressModel(evaporation, rfunc=ps.Exponential(), name="evap", settings="evap",
                                             up=None))
    else:
        model.add_stressmodel(ps.StressModel(precipitation, rfunc=ps.Exponential(), name="rain", settings="prec"))
        model.set_parameter("rain_a", initial=2.0, pmin=0.1, pmax=10.0)
        if form == "pulse_wet":
            model.add_stressmodel(ps.RechargeModel(precipitation, evaporation, rfunc=ps.Exponential(),
                                                   name="Recharge"))
            model.set_parameter("Recharge_A", initial=-1.0, pmin=-1000.0, pmax=0.0)
    if form in ("p_e_season", "pulse", "pulse_wet"):
        index = pd.date_range("1940-01-01", "2030-12-31", freq="D")
        sin, cos = harmonics(index)
        model.add_stressmodel(ps.StressModel(sin, rfunc=ps.One(), name="season_sin", settings="waterlevel"))
        model.add_stressmodel(ps.StressModel(cos, rfunc=ps.One(), name="season_cos", settings="waterlevel"))
    return model


def r2(observed: pd.Series, simulated: pd.Series) -> float:
    both = pd.concat([observed, simulated], axis=1).dropna()
    if len(both) < 30:
        return float("nan")
    o, s = both.iloc[:, 0], both.iloc[:, 1]
    return float(1 - ((o - s) ** 2).sum() / ((o - o.mean()) ** 2).sum())


def scores(observed: pd.Series, simulated: pd.Series, period: tuple, shift: bool = False) -> dict:
    o = observed.loc[period[0]:period[1]].dropna()
    s = simulated.reindex(o.index)
    bias = float(s.mean() - o.mean())
    # Another gauge has its own datum offset: there, compare the shape, not the level.
    s_shift = s - bias if shift else s
    annual = pd.concat([o.resample("YE").max(), s_shift.resample("YE").max()], axis=1, keys=["o", "s"]).dropna()
    return {"bias_cm": round(bias * 100, 2), "r2": round(r2(o, s_shift), 3), "rmse_cm": round(float(np.sqrt(((o - s_shift) ** 2).mean())) * 100, 2),
            "std_obs_cm": round(o.std() * 100, 2), "std_sim_cm": round(s_shift.std() * 100, 2),
            "annual_max_mae_cm": round(float((annual.o - annual.s).abs().median()) * 100, 1),
            "n_days": int(o.count())}


def fit_all(out: Path) -> tuple[pd.DataFrame, dict]:
    import pastas as ps  # noqa: F401

    precipitation, evaporation = data.load_forcing()
    beemster, purmer = stresses.boezem_level("beemster"), stresses.boezem_level("purmer_zuid")
    rows, models = [], {}
    for form in FORMS:
        model = build(form, beemster.loc[CALIBRATION[0]:CALIBRATION[1]].dropna(), precipitation, evaporation)
        model.solve(tmin=CALIBRATION[0], tmax=CALIBRATION[1], warmup=WARMUP_DAYS, report=False)
        simulated = model.simulate(tmin="2015-01-01", tmax=beemster.index[-1], warmup=WARMUP_DAYS)
        row = {"form": form, "n_param": int(model.parameters["vary"].sum()), "aic": round(model.stats.aic(), 1)}
        for label, obs, period in (("cal", beemster, CALIBRATION), ("val", beemster, VALIDATION),
                                   ("purmer_zuid", purmer, OTHER_GAUGE)):
            row.update({f"{label}_{k}": v for k, v in
                        scores(obs, simulated, period, shift=label == "purmer_zuid").items()})
        rows.append(row)
        models[form] = model
        model.to_file(str(out / f"boezem_emulator_{form}.pas"))
        simulated.rename("simulated").to_frame().join(beemster.rename("beemster")).join(
            purmer.rename("purmer_zuid")).to_csv(out / f"boezem_emulator_{form}_series.csv")
    return pd.DataFrame(rows), models


def synthetic_members(model, warmup_years: int = 5) -> dict[str, pd.Series]:
    """The emulator driven by every IMPREX member (m NAP, daily, every day of the member)."""
    from research.accepted.report import _members

    members, forcing = _members()
    out = {}
    for name, (precipitation, evaporation) in forcing.items():
        sms = model.stressmodels
        if "Recharge" in sms:
            sms["Recharge"].stress[0].series_original = precipitation
            sms["Recharge"].stress[1].series_original = evaporation
        if "rain" in sms:
            sms["rain"].stress[0].series_original = precipitation
        if "evap" in sms:
            sms["evap"].stress[0].series_original = evaporation
        # From the member's first day, so the groundwater model's own spin-up has a boezem too.
        out[name] = model.simulate(tmin=precipitation.index[0], tmax=precipitation.index[-1],
                                   warmup=warmup_years * 365)
    return out


def compare_climate(observed: pd.Series, synthetic: dict[str, pd.Series]) -> pd.DataFrame:
    pooled = pd.concat(synthetic.values())
    member_max = pd.concat([s.groupby(s.index.year).max() for s in synthetic.values()])
    rows = []
    observed = observed.dropna()
    for label, series, annual_max in (("observed 2019-2026", observed, observed.groupby(observed.index.year).max()),
                                      ("IMPREX emulated", pooled, member_max)):
        rows.append({"series": label, "years": int(annual_max.size), "mean": round(series.mean(), 3),
                     "std_cm": round(series.std() * 100, 2),
                     "p99": round(series.quantile(0.99), 3),
                     "annual_max_median": round(annual_max.median(), 3),
                     "annual_max_p90": round(annual_max.quantile(0.9), 3),
                     "max": round(series.max(), 3)})
    return pd.DataFrame(rows)


def main() -> None:
    warnings.filterwarnings("ignore")
    out = Path(sys.argv[1] if len(sys.argv) > 1 else PASTAS / "local_runs" / "research" / "boezem_emulator")
    out.mkdir(parents=True, exist_ok=True)
    table, models = fit_all(out)
    table.to_csv(out / "boezem_emulator_scores.csv", index=False)
    with pd.option_context("display.width", 250):
        print(table.T.to_string())
    # For return periods the wet tail matters: the smallest annual-maximum error out of sample.
    best = table.sort_values(["val_annual_max_mae_cm", "aic"]).iloc[0]["form"]
    print(f"\nchosen: {best}")
    synthetic = synthetic_members(models[best])
    pd.concat(synthetic, names=["member", "date"]).rename("boezem").to_csv(out / "boezem_synthetic_imprex.csv")
    climate = compare_climate(stresses.boezem_level("beemster"), synthetic)
    climate.to_csv(out / "boezem_emulator_climate.csv", index=False)
    print(climate.to_string(index=False))
    (out / "boezem_emulator.json").write_text(json.dumps({
        "chosen": best, "calibration": CALIBRATION, "validation": VALIDATION, "other_gauge": OTHER_GAUGE,
        "parameters": models[best].parameters["optimal"].round(6).to_dict()}, indent=2))


if __name__ == "__main__":
    main()
