"""Model variants: the pipeline's five types plus local forcing, extra stresses and a bucket.

A variant name is ``<base type>@<variant>``; the bare base type is the pipeline itself.
Every variant is a Pastas model, so the Brakenhoff checks, the selection and the IMPREX
simulation apply unchanged. The P/E stress model is always added first: the pipeline's
``t95_days`` and ``gain`` read the first stress model.

Variants
--------
``localP``    precipitation: inverse-distance weighted over the 3 nearest KNMI
              neerslagstations (08-08 UT sums moved one day back); evaporation Berkhout.
``localP0``   the same without the one-day shift (to test the shift).
``localPE``   ``localP`` plus Makkink weighted between Berkhout and Schiphol.
``wl``        + Markermeer level at RWS Edam: ``StressModel(level - mean, Exponential)``.
``wl1``       the same with an instantaneous response (``ps.One``): a lake beside a dike.
``wlmid``     + RWS Markermeer midden instead of Edam.
``wlhhnk``    + HHNK Drieban outer water (2019-2025; not ongoing).
``baro``      + air pressure (Schiphol PG - mean), instantaneous (``ps.One``), either sign.
``flex``      soil-moisture bucket: ``RechargeModel(recharge=FlexModel)`` on mm/day with
              ``lp``, ``simax`` and ``kv`` fixed at Pastas' defaults; base type
              ``flex_exp`` or ``flex_gamma``.
``bz``        + Schermerboezem level (HHNK gauge at Dijkgraaf de Boer, Beemster side, from
              2019-05; the same stress for Beemster and Purmer series) as is:
              ``StressModel(level - mean, Exponential)``.
``bz1``       the same, instantaneous (``ps.One``).
``bzn``       normalised: (level - mean) / std over the calibration days, Exponential.
``bzd``       the deviation from the managed set point (level minus its 31-day running
              median), normalised, Exponential: only the excursions, not the set-point steps.
``up``        + uitstroompeil (boezem side) of the series' nearest pumping station (HHNK
              15-minute records, cleaned, daily), instantaneous (``ps.One``).
``ip``        + instroompeil (polder side, the polder water level) of the nearest station.
``pos``       + by cross-section position: crest series (BUKR, KR, BIKR) get the
              uitstroompeil, berm and toe series (BIB, INBIB, KRBIB, BIT) the instroompeil.
              One rule for every series, so still one shared stress set. ``posx``: the same
              with an Exponential response.
``upfar``, ``ipfar``, ``posfar``  the same from the polder's *other* station (Beemster:
              Jacobus Bouman east / Wouter Sluis west; Heerhugowaard: Hensbroek / Huygendijk).
              Not applicable in the Purmer (one station).
``radar``     precipitation: KNMI gauge-adjusted daily radar (rad_nl25_rac_mfbs_24h) at the
              polder's point, Berkhout before the radar record starts; evaporation Berkhout.
``def<D0>``, ``roll<days>``, ``bkt<Smax>l<lp>``  + a drought indicator from the model's own P
              and E (``research/accepted/drought.py``): the KNMI potential deficit above D0 mm,
              the rolling E - P, or a root-zone bucket deficit. ``StressModel(indicator,
              Exponential, up=None)``: the gain sign is free (the head may drop more or less
              than the linear response says). On IMPREX it is recomputed per member.
Variants combine with ``+``: ``linear_gamma@localP+wl``.

Tarso allows no other stress model beside it, so the extra stresses only go on the
linear and flex types.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from research.accepted import drought
from tarso_pipeline.fit import RFUNCS, build_model

LINEAR = ("linear_exp", "linear_gamma", "linear_dexp", "linear_fourparam")
BASE_TYPES = ("tarso", *LINEAR)
FLEX = ("flex_exp", "flex_gamma")
EXTRA = {"wl": "rws_edam", "wl1": "rws_edam", "wlmid": "rws_markermeer.midden", "wlhhnk": "hhnk_drieban", "baro": "pressure",
         "bz": "boezem", "bz1": "boezem", "bzn": "boezem", "bzd": "boezem_dev",
         "up": "gemaal_up", "ip": "gemaal_ip", "pos": "gemaal_pos", "posx": "gemaal_pos",
         "upfar": "gemaal_up_far", "ipfar": "gemaal_ip_far", "posfar": "gemaal_pos_far"}
INSTANT = ("wl1", "bz1", "up", "ip", "pos", "upfar", "ipfar", "posfar")


def stress_name(part: str) -> str:
    """The stress model's name; distinct per kind so that kinds combine (``wl1+baro+pos``)."""
    for prefix, name in (("bz", "boezem"), ("up", "uitstroom"), ("ip", "instroom"), ("pos", "gemaal")):
        if part.startswith(prefix):
            return name
    return "level"
NORMALISED = ("bzn", "bzd")


@dataclass
class Inputs:
    """Everything a series' variants may use; precipitation and evaporation in m/day."""

    precipitation: pd.Series
    evaporation: pd.Series
    local_precipitation: pd.Series | None = None
    local_precipitation_noshift: pd.Series | None = None
    local_evaporation: pd.Series | None = None
    radar_precipitation: pd.Series | None = None
    extra: dict = field(default_factory=dict)  # name -> stress (already minus its mean)


def parse(name: str) -> tuple[str, list[str]]:
    base, _, rest = name.partition("@")
    return base, [v for v in rest.split("+") if v]


def applicable(name: str) -> bool:
    base, parts = parse(name)
    if base == "tarso" and any(p in EXTRA or drought.is_part(p) for p in parts):
        return False
    return base in BASE_TYPES or base in FLEX


def forcing_for(parts: list[str], inputs: Inputs) -> tuple[pd.Series, pd.Series]:
    precipitation, evaporation = inputs.precipitation, inputs.evaporation
    if "localP" in parts or "localPE" in parts:
        precipitation = inputs.local_precipitation
    if "localP0" in parts:
        precipitation = inputs.local_precipitation_noshift
    if "localPE" in parts:
        evaporation = inputs.local_evaporation
    if "radar" in parts:
        precipitation = inputs.radar_precipitation
    if precipitation is None or evaporation is None:
        raise ValueError("local forcing not available for this series")
    return precipitation, evaporation


def build(name: str, series_name: str, head: pd.Series, inputs: Inputs):
    """An unsolved Pastas model for variant ``name``."""
    import pastas as ps

    base, parts = parse(name)
    precipitation, evaporation = forcing_for(parts, inputs)
    if base in FLEX:
        model = ps.Model(head, name=series_name)
        rfunc = getattr(ps, RFUNCS[base.split("_", 1)[1]])()
        model.add_stressmodel(ps.RechargeModel(precipitation * 1000.0, evaporation * 1000.0, rfunc=rfunc,
                                               recharge=ps.rch.FlexModel(), name="Recharge"))
        model.set_parameter("Recharge_kv", initial=1.0, vary=False)
    else:
        model = build_model(base, series_name, head, precipitation, evaporation)
    for part in parts:
        if part not in EXTRA:
            continue
        stress = inputs.extra.get(EXTRA[part])
        if stress is None or stress.loc[head.index.min():head.index.max()].count() < 100:
            raise ValueError(f"{EXTRA[part]} does not cover this series")
        if part in NORMALISED:
            std = float(stress.loc[head.index.min():head.index.max()].std())
            stress = stress / std if std > 0 else stress
        name_sm = stress_name(part)
        if part in INSTANT:
            model.add_stressmodel(ps.StressModel(stress, rfunc=ps.One(), name=name_sm, settings="waterlevel"))
            model.set_parameter(f"{name_sm}_d", pmin=-5.0, pmax=5.0)
        elif part == "baro":
            model.add_stressmodel(ps.StressModel(stress, rfunc=ps.One(), name="baro", settings="waterlevel"))
            model.set_parameter("baro_d", pmin=-10.0, pmax=10.0)
        else:
            model.add_stressmodel(ps.StressModel(stress, rfunc=ps.Exponential(), name=name_sm,
                                                 settings="waterlevel"))
    for part in parts:
        if drought.is_part(part):
            indicator = drought.from_part(part, precipitation * 1000.0, evaporation * 1000.0)
            model.add_stressmodel(ps.StressModel(indicator, rfunc=ps.Exponential(), name="drought",
                                                 settings="evap", up=None))
    return model


def names(groups: list[str]) -> list[str]:
    """The variant names for groups like ``baseline``, ``localP``, ``wl``, ``flex``, ``localP+wl``."""
    out = []
    for group in groups:
        if group == "baseline":
            out += list(BASE_TYPES)
        elif group == "flex":
            out += list(FLEX)
        elif group.startswith("flex@"):
            out += [f"{f}@{group[5:]}" for f in FLEX]
        else:
            out += [f"{b}@{group}" for b in (*BASE_TYPES, *FLEX) if applicable(f"{b}@{group}")]
    return list(dict.fromkeys(out))
