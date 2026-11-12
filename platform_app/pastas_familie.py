"""
Pastas -> Familie kiezen: walk through the models one by one and set the statistical family
per tail (dry / wet) by eye.

Per model the page shows, top to bottom:

- how well the model performs: EVP / R² / RMSE and `ml.plots.results()` (diagnostics in an
  expander), drawn by `fit_pastas_models`;
- the return periods: empirical points against the exp / gpd / gev curves (`run_extremes`),
  QQ and the confidence band in an expander;
- the pick per tail, saved to `pastas/family_choice/<prefix>_family_choice.csv` (locally;
  the same file the Modellen page and the Boxplots use).

The figures come from `pastas/results_export/<prefix>/plots/` (export_results.py), the
tables live or from the export, like the Modellen page.
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from tarso_pipeline import results
from tarso_pipeline.paths import DATA_BASES, VALIDATED

from . import pastas_bron as bron
from .pastas_pagina import _familiekeuze, _herkomst, _keuze, _label, _lees, _namen

MODEL_KEY, BASIS_KEY, OPEN_KEY = "pf_model", "pf_basis", "pf_alleen_open"


def _status(selectie: pd.DataFrame, keuze: pd.DataFrame) -> pd.DataFrame:
    """Per (meetreeks_id, model_type): how many tails have a hand-set family (0, 1 or 2)."""
    if selectie.empty:
        return pd.DataFrame(columns=["meetreeks_id", "model_type", "gekozen"])
    fam = results.effective_family(selectie, keuze)
    return (fam.assign(gekozen=fam["family_source"].eq(results.GEKOZEN))
            .groupby(["meetreeks_id", "model_type"])["gekozen"].sum().astype(int).reset_index())


def _stap(opties: list[str], delta: int) -> None:
    """Previous / next model (an on_click callback: runs before the selectbox is drawn)."""
    huidig = st.session_state.get(MODEL_KEY)
    i = opties.index(huidig) if huidig in opties else -1
    st.session_state[MODEL_KEY] = opties[(i + delta) % len(opties)]


def _figuur(basis: str, rij, kind: str, onderschrift: str) -> bool:
    pad = bron.plot(basis, rij.series_name, kind)
    if pad is None:
        return False
    st.image(str(pad), caption=onderschrift)
    return True


def render() -> None:
    st.title("Familie kiezen")
    st.caption("Loop de modellen langs en kies per staart de statistische familie op basis van de plots. "
               "De keuze geldt ook voor de herhalingstijden in de Boxplots.")
    basis = st.segmented_control("Getraind op", list(DATA_BASES), default=VALIDATED, key=BASIS_KEY,
                                 format_func=bron.BASIS_LABEL.get) or VALIDATED

    metrics = _lees(basis, "fit_metrics")
    selectie = _lees(basis, "evt_selection").data
    if metrics.data.empty:
        st.info(f"Nog geen modellen voor **{bron.BASIS_LABEL[basis]}** · {_herkomst(metrics)}.")
        return
    tabel = metrics.data[metrics.data["success"].astype(str).str.lower().eq("true")]
    tabel = tabel.merge(_namen(), on="meetreeks_id", how="left")
    tabel = tabel.merge(_status(selectie, _keuze(basis)), on=["meetreeks_id", "model_type"], how="left")
    tabel["gekozen"] = tabel["gekozen"].fillna(0).astype(int)
    tabel = tabel.assign(_sort=tabel["code"].fillna(tabel["series_name"])).sort_values(
        ["_sort", "model_type"], ignore_index=True)

    klaar = int((tabel["gekozen"] == 2).sum())
    st.progress(klaar / len(tabel), text=f"{klaar} van {len(tabel)} modellen met een gekozen familie voor "
                                         f"beide staarten · {bron.prefix(basis)}_* · {_herkomst(metrics)}")
    if selectie.empty:
        st.warning("Geen extremenanalyse (`evt_selection`) voor deze basis: draai `run_extremes.py` en "
                   "`export_results.py` met dezelfde `data_basis`. Kiezen kan pas daarna.")

    alleen_open = st.toggle("Alleen modellen zonder volledige keuze", key=OPEN_KEY)
    zicht = tabel[tabel["gekozen"] < 2] if alleen_open else tabel
    if zicht.empty:
        st.success("Alle modellen hebben voor beide staarten een gekozen familie.")
        return
    sleutels = [f"{r.meetreeks_id}|{r.model_type}" for r in zicht.itertuples()]
    teken = {0: "○○", 1: "●○", 2: "●●"}
    labels = {f"{r.meetreeks_id}|{r.model_type}": f"{teken[r.gekozen]}  {_label(r)}" for r in zicht.itertuples()}
    if st.session_state.get(MODEL_KEY) not in sleutels:
        st.session_state[MODEL_KEY] = sleutels[0]

    k1, k2, k3 = st.columns([1, 8, 1], vertical_alignment="bottom")
    k1.button("◀ Vorige", on_click=_stap, args=(sleutels, -1), width="stretch")
    k2.selectbox(f"Model ({len(sleutels)}) · ●● = beide staarten gekozen", sleutels, key=MODEL_KEY,
                 format_func=labels.get)
    k3.button("Volgende ▶", on_click=_stap, args=(sleutels, 1), width="stretch")

    meetreeks_id, model_type = st.session_state[MODEL_KEY].split("|", 1)
    rij = next(zicht[(zicht["meetreeks_id"] == meetreeks_id) & (zicht["model_type"] == model_type)].itertuples())

    st.subheader(_label(rij))
    m = st.columns(4)
    m[0].metric("EVP", f"{rij.evp:.1f} %")
    m[1].metric("R²", f"{rij.r2:.3f}")
    m[2].metric("RMSE", f"{rij.rmse:.3f} m")
    m[3].metric("Waarnemingen", f"{int(rij.nobs)}")
    st.caption(f"{rij.tmin} t/m {rij.tmax} · run `{rij.run_id}` · `{rij.series_name}`")

    st.markdown("#### Hoe goed is het model")
    if not _figuur(basis, rij, "results", "ml.plots.results()"):
        st.caption("Geen resultatenplot in de export: draai `export_results.py` (met `results` in `plot_kinds`) "
                   "en push `pastas/results_export/`.")
    if bron.plot(basis, rij.series_name, "diagnostics"):
        with st.expander("Diagnostiek van de residuen (ml.plots.diagnostics())"):
            _figuur(basis, rij, "diagnostics", "ml.plots.diagnostics()")

    st.markdown("#### Herhalingstijden")
    if not _figuur(basis, rij, "return_periods", "Empirisch (punten) tegen de exp-, gpd- en gev-curve, "
                                                 "links droog, rechts nat"):
        st.caption("Geen herhalingstijdenplot in de export voor dit model.")
    extra = [(k, t) for k, t in (("qq", "QQ-plot per familie"), ("confidence", "GEV met 95%-band"))
             if bron.plot(basis, rij.series_name, k)]
    if extra:
        with st.expander("Meer: " + ", ".join(t for _, t in extra)):
            for k, t in extra:
                _figuur(basis, rij, k, t)

    st.markdown("#### Familie per staart")
    levels = _lees(basis, "return_levels", rij.meetreeks_id).data
    if selectie.empty or levels.empty:
        st.info("Nog geen herhalingstijden voor dit model.")
        return
    _familiekeuze(basis, rij, selectie, levels[levels["model_type"] == rij.model_type])
