"""
Return levels from the Pastas models, drawn per well in the Boxplots cross section.

For every well of the cross section, the series Boxplots shows for it (the same choice as
the heads, `databricks_reeksen.kies_reeksen`) is looked up in the Pastas results; its
return levels in the family picked per tail (`pastas/family_choice/`, set on Pastas ->
Familie kiezen) are drawn as one line per tail and return period across the wells: solid
for the wet tail (high heads), dashed for the dry tail, a triangle at each well. A tail
without a pick has no family and is left out. The figure from boxplots is copied, never
changed, so its cache stays valid; the new traces come last, so the time slider (which
restyles traces by index) is unaffected.
"""

from __future__ import annotations

import dataclasses

import plotly.graph_objects as go
import streamlit as st
from pandas import DataFrame

from tarso_pipeline import results
from tarso_pipeline.paths import DATA_BASES, VALIDATED

from . import pastas_bron as bron

MODEL_TYPE = "tarso"
STANDAARD_T = [10, 100]
# Light to dark per return period, one ramp per tail (blue wet, orange dry).
RAMP = {"wet": ["#9ec5f0", "#5b9be3", "#2a78d6", "#1a56a3", "#0e3870"],
        "dry": ["#f5c09f", "#f09461", "#eb6834", "#b9471a", "#7d2c0c"]}
SYMBOOL = {"wet": "triangle-up", "dry": "triangle-down"}


def punten(putten: DataFrame, niveaus: DataFrame, perioden: list[int]) -> DataFrame:
    """One row per well x tail x T: x_profiel, peilbuis, level, family.

    `putten` has meetreeks_id, x_profiel, peilbuis, diepte_niveau (the Stijghoogtes
    catalogue); `niveaus` is `results.chosen_levels(...)`.
    """
    kolommen = ["x_profiel", "peilbuis", "diepte_niveau", "tail", "return_period", "level", "family",
                "meetreeks_id"]
    if putten.empty or niveaus.empty:
        return DataFrame(columns=kolommen)
    lv = niveaus[(niveaus["model_type"] == MODEL_TYPE) & niveaus["return_period"].isin(perioden)]
    uit = putten.dropna(subset=["meetreeks_id", "x_profiel"]).merge(lv, on="meetreeks_id", how="inner")
    return uit.dropna(subset=["level"])[kolommen].sort_values(["tail", "return_period", "x_profiel"],
                                                             ignore_index=True)


def _kleur(staart: str, i: int, n: int) -> str:
    """The i-th of n return periods, spread over the tail's ramp (one period: the middle)."""
    ramp = RAMP[staart]
    return ramp[len(ramp) // 2] if n == 1 else ramp[round(i * (len(ramp) - 1) / (n - 1))]


def _lijn(g: DataFrame) -> DataFrame:
    """The points of one line: per x position the mean level, and every well there for the hover.

    Two filters of one well share an x; the line goes through their mean instead of zigzagging.
    """
    return (g.groupby("x_profiel", sort=True)
            .agg(level=("level", "mean"),
                 tekst=("peilbuis", lambda p: ", ".join(dict.fromkeys(p))),
                 familie=("family", lambda f: ", ".join(dict.fromkeys(f))))
            .reset_index())


def voeg_toe(figuur: go.Figure, pt: DataFrame) -> go.Figure:
    """A copy of `figuur` with one line per tail x return period across the wells, and room for them."""
    fig = go.Figure(figuur)
    if pt.empty:
        return fig
    perioden = sorted(pt["return_period"].unique())
    for staart in ("wet", "dry"):
        for i, T in enumerate(perioden):
            g = pt[(pt["tail"] == staart) & (pt["return_period"] == T)]
            if g.empty:
                continue
            lijn = _lijn(g)
            kleur = _kleur(staart, i, len(perioden))
            fig.add_trace(go.Scatter(
                x=lijn["x_profiel"], y=lijn["level"], mode="lines+markers", name=f"T={T}",
                legendgroup=f"herhaling_{staart}",
                legendgrouptitle_text=f"Herhalingstijd {'nat' if staart == 'wet' else 'droog'} (Pastas)",
                line={"color": kleur, "width": 2.5, "dash": "solid" if staart == "wet" else "dash"},
                marker={"symbol": SYMBOOL[staart], "size": 11, "color": kleur, "line": {"width": 1, "color": "#222"}},
                customdata=lijn[["tekst", "familie"]].to_numpy(dtype=object),
                hovertemplate=(f"%{{customdata[0]}} · T={T} {'nat' if staart == 'wet' else 'droog'}: "
                               "%{y:.2f} m NAP<br>familie %{customdata[1]}<extra></extra>"),
            ))
    # The profile figure fixes its y range; widen it so every line is in view.
    bereik = fig.layout.yaxis.range
    if bereik is not None:
        laag = min(bereik[0], float(pt["level"].min()) - 0.3)
        hoog = max(bereik[1], float(pt["level"].max()) + 0.3)
        fig.update_yaxes(range=[laag, hoog])
    return fig


def _putten(project: str, profiel_id: str) -> DataFrame:
    from . import bron as bron_module
    from .stijghoogtes import _catalogus

    b = bron_module.bron()
    cat = _catalogus(bron_module.versie(b), b)
    return cat[(cat["project"] == project) & (cat["dwarsprofiel"] == profiel_id)]


def in_profiel(figuren, project: str, profiel_id: str):
    """Controls above the Boxplots plot; returns `figuren` with the return levels added (or as is)."""
    from .pastas_pagina import _keuze, _lees

    k1, k2, k3 = st.columns([1, 2, 2], vertical_alignment="bottom")
    aan = k1.toggle("Herhalingstijden", value=True, help="Uit de Pastas-modellen van de peilbuizen in dit "
                    "dwarsprofiel, in de statistische familie die per staart is gekozen (Pastas → Familie kiezen).")
    if not aan:
        return figuren
    basis = k2.segmented_control("Pastas getraind op", list(DATA_BASES), default=VALIDATED,
                                 format_func=lambda b: bron.BASIS_LABEL[b].split(" (")[0], key="bp_herh_basis") \
        or VALIDATED
    lv = _lees(basis, "return_levels")
    if lv.data.empty:
        st.caption(f"Geen herhalingstijden ({bron.BASIS_LABEL[basis].lower()}): "
                   f"{lv.melding or 'nog geen extremenanalyse'}.")
        return figuren
    beschikbaar = sorted(int(t) for t in lv.data["return_period"].unique())
    perioden = k3.multiselect("Herhalingstijden (jaar)", beschikbaar,
                              default=[t for t in STANDAARD_T if t in beschikbaar], key="bp_herh_T")
    try:
        putten = _putten(project, profiel_id)
    except Exception as fout:  # noqa: BLE001 - the plot itself must still show
        st.caption(f"Peilbuizen van dit dwarsprofiel niet gevonden: {fout}")
        return figuren
    pt = punten(putten, results.chosen_levels(lv.data, _keuze(basis)), perioden)
    met = pt["peilbuis"].nunique() if not pt.empty else 0
    zonder = sorted(set(putten.dropna(subset=["meetreeks_id"])["peilbuis"]) - set(pt["peilbuis"]))
    st.caption(f"Herhalingstijden voor {met} van {putten['peilbuis'].nunique()} peilbuizen · "
               f"{'live' if lv.bron == bron.LIVE else 'export'}"
               + (f" · zonder model of gekozen familie: {', '.join(zonder)}" if zonder else ""))
    if pt.empty:
        return figuren
    return dataclasses.replace(figuren, profiel_figuur=voeg_toe(figuren.profiel_figuur, pt))


__all__ = ["in_profiel", "punten", "voeg_toe"]
