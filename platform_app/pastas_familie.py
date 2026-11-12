"""
Pastas -> Familie kiezen: walk through the models one by one and set the statistical family
per tail (dry / wet) by eye.

Per model the page shows, top to bottom:

- how well the model performs: EVP / R² / RMSE and `ml.plots.results()` (diagnostics in an
  expander), drawn by `fit_pastas_models`;
- the return periods: empirical points against the exp / gpd / gev curves (`run_extremes`),
  QQ and the confidence band in an expander;
- the pick per tail, saved to `pastas/family_choice/<prefix>_family_choice.csv` as soon as it
  changes (locally; the same file the Modellen page and the Boxplots use). Only picks count:
  a tail without one gets no return levels.

The figures come from `pastas/results_export/<prefix>/plots/` (export_results.py), the
tables live or from the export, like the Modellen page.
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from tarso_pipeline import results
from tarso_pipeline.evt import EMPIRICAL_MAX_T
from tarso_pipeline.paths import DATA_BASES, VALIDATED

from . import pastas_bron as bron
from .pastas_pagina import _familiekeuze, _herkomst, _keuze, _label, _lees, _namen

MODEL_KEY, BASIS_KEY, OPEN_KEY = "pf_model", "pf_basis", "pf_alleen_open"
TABEL_KEY, TABEL_SLEUTELS = "pf_overzicht", "pf_overzicht_sleutels"
STATUS = {0: "open", 1: "half", 2: "klaar"}


def _status(keuze: pd.DataFrame) -> pd.DataFrame:
    """Per (meetreeks_id, model_type): how many tails have a valid picked family (0, 1 or 2)."""
    gekozen = keuze[keuze["family"].isin(results.PICKABLE)].drop_duplicates(["meetreeks_id", "model_type", "tail"])
    return gekozen.groupby(["meetreeks_id", "model_type"]).size().rename("gekozen").reset_index()


def overzicht(tabel: pd.DataFrame, keuze: pd.DataFrame) -> pd.DataFrame:
    """One row per model: the family, note and who picked it, per tail, and how far it is.

    `tabel` is the models (meetreeks_id, model_type, series_name, profielnaam, code, cs_naam), `keuze`
    the family_choice file. A tail without a pick shows "–".
    """
    keys = ["meetreeks_id", "model_type"]
    gekozen = keuze[keuze["family"].fillna("") != ""].drop_duplicates([*keys, "tail"], keep="last")
    uit = tabel[[*keys, "series_name", *[c for c in ("profielnaam", "code", "cs_naam") if c in tabel]]].copy()
    for staart, naam in (("dry", "droog"), ("wet", "nat")):
        deel = gekozen[gekozen["tail"] == staart][[*keys, "family", "n_kept", "note", "chosen_by",
                                                    "chosen_at"]].rename(
            columns={"family": naam, "n_kept": f"drempel {naam}", "note": f"notitie {naam}",
                     "chosen_by": f"door {naam}", "chosen_at": f"op {naam}"})
        uit = uit.merge(deel, on=keys, how="left")
    n = sum(uit[naam].isin(results.PICKABLE).astype(int) for naam in ("droog", "nat"))
    uit.insert(0, "status", n.map(STATUS))
    for naam in ("droog", "nat"):
        uit[naam] = uit[naam].map(lambda f: "–" if not isinstance(f, str) else results.FAMILY_LABELS[f]
                                  if f in results.PICKABLE else f"{f} (opnieuw kiezen)")
        uit[f"drempel {naam}"] = uit[f"drempel {naam}"].fillna("").replace("", "standaard").where(
            uit[naam] != "–", "")
        uit[f"notitie {naam}"] = uit[f"notitie {naam}"].fillna("")
        uit[f"door {naam}"] = uit[f"door {naam}"].fillna("")
        uit[f"op {naam}"] = uit[f"op {naam}"].fillna("").str[:16].str.replace("T", " ")
    return uit


def _naar_rij() -> None:
    """on_select of the overview: jump to the model of the clicked row."""
    keuze = st.session_state.get(TABEL_KEY)
    rijen = keuze.selection.rows if keuze is not None and keuze.selection else []
    sleutels = st.session_state.get(TABEL_SLEUTELS, [])
    if rijen and rijen[0] < len(sleutels):
        st.session_state[MODEL_KEY] = sleutels[rijen[0]]


def _overzichtstabel(zicht: pd.DataFrame, keuze: pd.DataFrame, basis: str) -> None:
    tab = overzicht(zicht, keuze)
    st.session_state[TABEL_SLEUTELS] = [f"{r.meetreeks_id}|{r.model_type}" for r in tab.itertuples()]
    telling = tab["status"].value_counts()
    titel = (f"Overzicht · {telling.get('klaar', 0)} klaar, {telling.get('half', 0)} half, "
             f"{telling.get('open', 0)} open")
    with st.expander(titel, expanded=True):
        kolommen = [c for c in ("status", "profielnaam", "code", "cs_naam", "series_name", "droog", "drempel droog", "nat",
                                "drempel nat", "notitie droog",
                                "notitie nat", "door droog", "door nat", "op droog", "op nat") if c in tab]
        st.dataframe(tab[kolommen], hide_index=True, width="stretch", height=min(38 + 35 * len(tab), 330),
                     on_select=_naar_rij, selection_mode="single-row", key=TABEL_KEY, column_config={
                         "status": st.column_config.TextColumn("status", help="klaar: beide staarten gekozen, "
                                                               "half: één, open: geen"),
                         "cs_naam": "locatie", "profielnaam": "dwarsprofiel"})
        st.caption("Klik een rij om naar dat model te gaan.")
        st.download_button("Download als CSV", tab.to_csv(index=False).encode("utf-8"),
                           file_name=f"{bron.prefix(basis)}_familiekeuze_overzicht.csv", mime="text/csv")


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
    if metrics.data.empty:
        st.info(f"Nog geen modellen voor **{bron.BASIS_LABEL[basis]}** · {_herkomst(metrics)}.")
        return
    tabel = metrics.data[metrics.data["success"].astype(str).str.lower().eq("true")]
    tabel = tabel.merge(_namen(), on="meetreeks_id", how="left")
    tabel = tabel.merge(_status(_keuze(basis)), on=["meetreeks_id", "model_type"], how="left")
    tabel["gekozen"] = tabel["gekozen"].fillna(0).astype(int)
    tabel = tabel.assign(_sort=tabel.get("profielnaam", pd.Series(index=tabel.index, dtype=object))
                         .fillna(tabel["code"]).fillna(tabel["series_name"])).sort_values(
        ["_sort", "model_type"], ignore_index=True)

    klaar = int((tabel["gekozen"] == 2).sum())
    st.progress(klaar / len(tabel), text=f"{klaar} van {len(tabel)} modellen met een gekozen familie voor "
                                         f"beide staarten · {bron.prefix(basis)}_* · {_herkomst(metrics)}")

    alleen_open = st.toggle("Alleen modellen zonder volledige keuze", key=OPEN_KEY)
    zicht = tabel[tabel["gekozen"] < 2] if alleen_open else tabel
    if zicht.empty:
        st.success("Alle modellen hebben voor beide staarten een gekozen familie.")
        return
    _overzichtstabel(zicht, _keuze(basis), basis)
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

    st.markdown("#### Herhalingstijden en drempel")
    if not _figuur(basis, rij, "return_periods", "Links droog, rechts nat. Grijs vlak: tot T=100 geldt het "
                                                 "empirische niveau; daarna de gekozen familie."):
        st.caption("Geen herhalingstijdenplot in de export voor dit model.")
    _figuur(basis, rij, "threshold", "Drempelkeuze: waar de GPD-vorm (boven) niet meer verandert, is de drempel "
                                     "hoog genoeg. Onder: het T=1000-niveau per drempel.")
    extra = [(k, t) for k, t in (("qq", "QQ-plot per familie"), ("confidence", "GPD en exponentieel met 95%-band"))
             if bron.plot(basis, rij.series_name, k)]
    if extra:
        with st.expander("Meer: " + ", ".join(t for _, t in extra)):
            for k, t in extra:
                _figuur(basis, rij, k, t)

    st.markdown(f"#### Familie per staart (na T={EMPIRICAL_MAX_T})")
    levels = _lees(basis, "return_levels", rij.meetreeks_id).data
    if levels.empty:
        st.info("Nog geen herhalingstijden voor dit model: draai `run_extremes.py` en `export_results.py` met "
                "dezelfde `data_basis`.")
        return
    _familiekeuze(basis, rij, levels[levels["model_type"] == rij.model_type])
