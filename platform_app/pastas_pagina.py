"""
The Pastas section: the fitted models, their plots and return periods, and the family pick.

- Modellen: every model of one data basis (unvalidated / validated) in a table; pick one to
  see observed vs. simulated head, the Brakenhoff checks and the return levels of each
  family, and to pick the statistical family per tail (dry / wet).
- Vergelijken: the same series trained on validated and on unvalidated data, side by side.
- Familie kiezen (pastas_familie.py): one model at a time, its fit and return-period plots
  and the pick per tail, with previous / next.

Results come live from Databricks when the app may read them, otherwise from the export in
git (platform_app/pastas_bron.py). The family pick is saved to
pastas/family_choice/<prefix>_family_choice.csv when the app runs locally (like the review
decisions: a Databricks App's files are not kept); load_family_choice.py takes it to Delta.
"""

from __future__ import annotations

import getpass
from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from pandas import DataFrame

from review_app import common
from tarso_pipeline import results, selection
from tarso_pipeline.evt import EMPIRICAL_MAX_T, INDICATIVE_MIN_T
from tarso_pipeline.fit import DEFAULT_MODEL_TYPES, MODEL_LABELS
from tarso_pipeline.paths import DATA_BASES, UNVALIDATED, VALIDATED

from . import pastas_bron as bron
from .stijghoogtes import _tijdas

MODEL_PARAM, BASIS_PARAM = "model", "basis"
KLEUR = {"observed": "#4a4a4a", "simulated": "#2a78d6", "ander": "#eb6834"}
FAMILIE_KLEUR = {"gpd": "#2a78d6", "exp": "#eb6834", "gev": "#8a8984", "emp": "#0b0b0b"}
STAART_KLEUR = {"dry": "#eb6834", "wet": "#2a78d6"}
GEEN = "geen"  # no pick: the tail gets no return levels


@st.cache_data(ttl=600, show_spinner="Pastas-resultaten laden…", max_entries=40)
def _lees(basis: str, kind: str, meetreeks_id: str | None = None) -> bron.Resultaat:
    return bron.lees(basis, kind, meetreeks_id)


def _keuze(basis: str) -> DataFrame:
    """Not cached: it changes on every save."""
    return bron.lees_keuze(basis)


def _modelkeuze(basis: str) -> DataFrame:
    """Not cached: it changes on every save."""
    return bron.lees_modelkeuze(basis)


def _in_gebruik(basis: str) -> DataFrame:
    """Per series the model it uses: meetreeks_id, model_type, bron (handmatig / automatisch / standaard)."""
    metrics = _lees(basis, "fit_metrics").data
    reeksen = metrics["meetreeks_id"] if not metrics.empty else None
    return selection.model_in_use(_lees(basis, "model_selection").data, _modelkeuze(basis), reeksen)


@st.cache_data(ttl=600, show_spinner=False)
def _profielen(_versie: float) -> DataFrame:
    """meetreeks_id -> dwarsprofiel and profielnaam (BE_0072_BUKR_PB1), the way the cross
    sections link wells to series. The session's heads when they are loaded, else the export."""
    from modules import databricks_reeksen

    from . import bron as bron_module
    from .stijghoogtes_data import profielnamen_uit_reeksen

    if bron_module.SESSIE_BRON in st.session_state:
        reeksen = st.session_state[bron_module.SESSIE_BRON].reeksen
    elif databricks_reeksen.export_beschikbaar():
        reeksen = databricks_reeksen.lees_reeksen()
    else:
        reeksen = bron_module.bron().reeksen
    return profielnamen_uit_reeksen(reeksen)


def _namen() -> DataFrame:
    """meetreeks_id -> code, location name and cross section."""
    meta = common.object_data()
    namen = meta[["meetreeks_id", "code", "cs_naam", "vendor", "filename"]].drop_duplicates("meetreeks_id")
    try:
        from modules import paths

        profielen = _profielen(paths.config_csv().stat().st_mtime)
    except Exception:  # noqa: BLE001 - without the cross sections the table still works
        return namen
    return namen.merge(profielen[["meetreeks_id", "dwarsprofiel", "profielnaam"]], on="meetreeks_id", how="left")


def _label(rij) -> str:
    naam = rij.cs_naam if isinstance(getattr(rij, "cs_naam", None), str) else ""
    code = rij.code if isinstance(getattr(rij, "code", None), str) else rij.series_name
    profiel = rij.profielnaam if isinstance(getattr(rij, "profielnaam", None), str) else ""
    return " · ".join(d for d in (profiel, code, naam, rij.model_type) if d)


def _kies_basis(key: str) -> str:
    gevraagd = st.query_params.get(BASIS_PARAM, UNVALIDATED)
    basis = st.segmented_control("Getraind op", list(DATA_BASES), default=gevraagd if gevraagd in DATA_BASES
                                 else UNVALIDATED, format_func=bron.BASIS_LABEL.get, key=key) or UNVALIDATED
    st.query_params[BASIS_PARAM] = basis
    return basis


def _herkomst(r: bron.Resultaat) -> str:
    wat = {bron.LIVE: "live uit Databricks", bron.EXPORT: "uit de export in git", bron.GEEN: "niet beschikbaar"}[r.bron]
    return wat + (f" ({r.melding})" if r.melding else "")


def _overzicht(basis: str) -> DataFrame | None:
    metrics = _lees(basis, "fit_metrics")
    if metrics.data.empty:
        st.info(f"Nog geen modellen voor **{bron.BASIS_LABEL[basis]}** · {_herkomst(metrics)}. Draai in Databricks "
                f"`pastas/notebooks/transformation/fit_pastas_models.py` met `data_basis={basis}`; zonder live "
                "toegang daarna `analysis/export_results.py` en push `pastas/results_export/`.")
        return None
    st.caption(f"{bron.prefix(basis)}_* · {_herkomst(metrics)}")
    tabel = metrics.data.merge(_namen(), on="meetreeks_id", how="left")

    checks = _lees(basis, "model_checks").data
    if not checks.empty:
        per = (checks.assign(ok=checks["passed"].astype(str).str.lower().eq("true"))
               .groupby(["meetreeks_id", "model_type"])["ok"].agg(checks_ok="sum", checks="size").reset_index())
        tabel = tabel.merge(per, on=["meetreeks_id", "model_type"], how="left")
    keuze = _keuze(basis)
    for staart in ("dry", "wet"):
        deel = keuze[keuze["tail"] == staart].rename(columns={"family": f"familie_{staart}"})
        tabel = tabel.merge(deel[["meetreeks_id", "model_type", f"familie_{staart}"]],
                            on=["meetreeks_id", "model_type"], how="left")
        tabel[f"familie_{staart}"] = tabel[f"familie_{staart}"].replace("", None).fillna("–")
    return voeg_selectie_toe(tabel, _lees(basis, "model_selection").data, _in_gebruik(basis))


SELECTIE_KOLOMMEN = ["passed", "pass_runs", "runaway", "d_aic", "d_aic_tarso", "selected", "reason"]


def voeg_selectie_toe(tabel: DataFrame, selectie: DataFrame, in_gebruik: DataFrame) -> DataFrame:
    """The stamp, the runaway check and which model the series uses, per row of `tabel`.

    `stempel` is "passed" when the model passed R², memory, gain and bounds (the runs test is
    shown apart); `in_gebruik` is "✓ automatisch" / "✓ handmatig" / "✓ standaard" on the model
    the series uses, else "".
    """
    sleutel = ["meetreeks_id", "model_type"]
    tabel = tabel.drop(columns=[c for c in (*SELECTIE_KOLOMMEN, "stempel", "in_gebruik") if c in tabel])
    if not selectie.empty:
        deel = selectie[[*sleutel, *[c for c in SELECTIE_KOLOMMEN if c in selectie]]]
        tabel = tabel.merge(deel.drop_duplicates(sleutel, keep="last"), on=sleutel, how="left")
    geslaagd = tabel["passed"].astype(str).str.lower().eq("true") if "passed" in tabel else False
    tabel["stempel"] = pd.Series(geslaagd, index=tabel.index).map({True: "passed", False: ""})
    gebruikt = in_gebruik.rename(columns={"bron": "in_gebruik"})[[*sleutel, "in_gebruik"]]
    tabel = tabel.merge(gebruikt, on=sleutel, how="left")
    tabel["in_gebruik"] = ("✓ " + tabel["in_gebruik"]).fillna("")
    return tabel


def _plot_simulatie(basis: str, rij) -> None:
    sim = _lees(basis, "simulations", rij.meetreeks_id).data
    sim = sim[sim["model_type"] == rij.model_type] if not sim.empty else sim
    ander = VALIDATED if basis == UNVALIDATED else UNVALIDATED
    toon_ander = st.toggle(f"Ook het model op {bron.BASIS_LABEL[ander].lower()}", value=False,
                           key=f"pa_ander_{rij.meetreeks_id}")
    if sim.empty:
        st.info("Geen gesimuleerde reeks voor dit model (de simulaties worden geschreven sinds de fit-notebook "
                "`<prefix>_simulations` vult; draai de fit opnieuw).")
        return
    sim = sim.assign(datum=pd.to_datetime(sim["datum"])).sort_values("datum")
    fig = go.Figure()
    obs = sim.dropna(subset=["observed"])
    fig.add_trace(go.Scatter(x=obs["datum"], y=obs["observed"], mode="markers", name="gemeten",
                             marker={"color": KLEUR["observed"], "size": 3},
                             hovertemplate="%{x|%d-%m-%Y}: %{y:.3f} gemeten<extra></extra>"))
    fig.add_trace(go.Scatter(x=sim["datum"], y=sim["simulated"], name=f"gesimuleerd ({basis})",
                             line={"color": KLEUR["simulated"], "width": 2},
                             hovertemplate="%{x|%d-%m-%Y}: %{y:.3f} gesimuleerd<extra></extra>"))
    if toon_ander:
        andere = _lees(ander, "simulations", rij.meetreeks_id).data
        andere = andere[andere["model_type"] == rij.model_type] if not andere.empty else andere
        if andere.empty:
            st.caption(f"Geen model op {bron.BASIS_LABEL[ander].lower()} voor deze reeks.")
        else:
            andere = andere.assign(datum=pd.to_datetime(andere["datum"])).sort_values("datum")
            fig.add_trace(go.Scatter(x=andere["datum"], y=andere["simulated"], name=f"gesimuleerd ({ander})",
                                     line={"color": KLEUR["ander"], "width": 1.5, "dash": "dash"}))
    _tijdas(fig)
    fig.update_layout(hovermode="x unified", title_text=f"Gemeten en gesimuleerd · {rij.model_type}")
    st.plotly_chart(fig, config={"displaylogo": False})


def _drempels(levels: DataFrame, staart: str) -> tuple[list[int], int | None]:
    """The thresholds (n_kept) the extremes run fitted for this tail, and its default one."""
    if "n_kept" not in levels:
        return [], None
    deel = levels[(levels["tail"] == staart) & levels["family"].isin(results.PICKABLE)]
    opties = sorted(int(k) for k in pd.to_numeric(deel["n_kept"], errors="coerce").dropna().unique())
    standaard = deel[deel["default_kept"].astype(str).str.lower().eq("true")]["n_kept"] if "default_kept" in deel else []
    return opties, (int(standaard.iloc[0]) if len(standaard) else (opties[len(opties) // 2] if opties else None))


def _curve(levels: DataFrame, staart: str, familie: str, n_kept: int | None) -> DataFrame:
    deel = levels[(levels["tail"] == staart) & (levels["family"] == familie)]
    if n_kept is not None and "n_kept" in deel and familie in results.PICKABLE:
        deel = deel[pd.to_numeric(deel["n_kept"], errors="coerce") == n_kept]
    return deel.sort_values("return_period")


def _plot_herhaling(levels: DataFrame, staart: str, familie: str | None, n_kept: int | None = None) -> go.Figure:
    """Empirical up to T=100 (shaded: that is what is used there), the GPD and exponential at
    the threshold ``n_kept`` past it, the GEV on all years for comparison; the pick in bold."""
    fig = go.Figure()
    fig.add_vrect(x0=2, x1=EMPIRICAL_MAX_T, fillcolor="#efeeeb", opacity=0.6, line_width=0, layer="below",
                  annotation_text=f"T ≤ {EMPIRICAL_MAX_T}: empirisch", annotation_position="top left",
                  annotation_font_size=10)
    fig.add_vline(x=INDICATIVE_MIN_T, line_color="#c3c2b7", line_width=1, annotation_text="indicatief",
                  annotation_position="top left", annotation_font_size=10)
    for fam in ("emp", "gev", *results.PICKABLE):
        g = _curve(levels, staart, fam, n_kept).dropna(subset=["level"])
        if g.empty:
            continue
        gekozen = fam == familie
        fig.add_trace(go.Scatter(
            x=g["return_period"], y=g["level"], mode="lines+markers",
            name=results.FAMILY_LABELS.get(fam, fam) + (" ◀ gekozen" if gekozen else ""),
            line={"color": FAMILIE_KLEUR.get(fam, "#7f7f7f"), "width": 3.5 if gekozen else 1.4,
                  "dash": {"emp": "solid", "gev": "dot"}.get(fam, "solid" if gekozen else "dash")},
            hovertemplate="T=%{x}: %{y:.3f} m NAP<extra>" + fam + "</extra>"))
    fig.update_xaxes(type="log", title="herhalingstijd T (jaar)")
    fig.update_yaxes(title="stijghoogte (m NAP)")
    fig.update_layout(height=400, margin={"l": 10, "r": 10, "t": 40, "b": 10},
                      title={"text": results.TAIL_LABELS[staart], "x": 0, "font": {"size": 14}},
                      legend={"orientation": "h", "y": -0.3})
    return fig


def _bewaar_keuze(basis: str, meetreeks_id: str, series_name: str, model_type: str, staart: str,
                  sleutel_familie: str, sleutel_drempel: str, sleutel_notitie: str) -> None:
    """on_change of the pick, its threshold or its note: write it to the family_choice CSV straight away."""
    familie = st.session_state[sleutel_familie]
    n_kept = st.session_state.get(sleutel_drempel)
    nieuw = results.set_family_choice(
        _keuze(basis), meetreeks_id=meetreeks_id, series_name=series_name, model_type=model_type, tail=staart,
        family=None if familie == GEEN else familie, n_kept=None if familie == GEEN else n_kept,
        note=st.session_state.get(sleutel_notitie, ""), chosen_by=getpass.getuser(),
        chosen_at=datetime.now(ZoneInfo("Europe/Amsterdam")).isoformat(timespec="seconds"))
    results.write_family_choice(bron.keuze_pad(basis), nieuw)
    st.toast(f"{results.TAIL_LABELS[staart].split(' ')[0].capitalize()}: "
             f"{'geen keuze' if familie == GEEN else results.FAMILY_LABELS[familie]}"
             f"{'' if familie == GEEN or n_kept is None else f' · {n_kept} jaren boven de drempel'} · opgeslagen in "
             f"{bron.keuze_pad(basis).relative_to(bron.ROOT).as_posix()}")


def _familiekeuze(basis: str, rij, levels: DataFrame) -> None:
    """Per tail: the curves, and the pick (family + threshold), saved as soon as it changes."""
    keuze = _keuze(basis)
    eigen = keuze[(keuze["meetreeks_id"] == rij.meetreeks_id) & (keuze["model_type"] == rij.model_type)]
    gekozen = {r.tail: r for r in eigen.itertuples() if r.family}
    alleen_lezen = common.read_only()
    for kolom, staart in zip(st.columns(2), ("dry", "wet")):
        with kolom:
            pick = gekozen.get(staart)
            if levels[levels["tail"] == staart].empty:
                st.caption(f"{results.TAIL_LABELS[staart]}: geen herhalingstijden.")
                continue
            oud = pick is not None and pick.family not in results.PICKABLE
            familie = pick.family if pick is not None and not oud else GEEN
            opties, standaard = _drempels(levels, staart)
            gekozen_kept = pd.to_numeric(getattr(pick, "n_kept", ""), errors="coerce") if pick is not None else None
            n_kept = int(gekozen_kept) if gekozen_kept is not None and pd.notna(gekozen_kept) else standaard

            sleutel = f"pa_fam_{basis}_{rij.meetreeks_id}_{rij.model_type}_{staart}"
            sleutel_drempel = f"pa_drempel_{basis}_{rij.meetreeks_id}_{rij.model_type}_{staart}"
            sleutel_notitie = f"pa_not_{basis}_{rij.meetreeks_id}_{rij.model_type}_{staart}"
            # The widgets follow the file: a save elsewhere (other page, git pull) shows up here.
            st.session_state[sleutel] = familie
            if opties:
                st.session_state[sleutel_drempel] = n_kept if n_kept in opties else standaard
            st.session_state.setdefault(sleutel_notitie, pick.note if pick is not None else "")
            opslaan = {"on_change": _bewaar_keuze, "disabled": alleen_lezen,
                       "args": (basis, rij.meetreeks_id, rij.series_name, rij.model_type, staart, sleutel,
                                sleutel_drempel, sleutel_notitie)}

            st.plotly_chart(_plot_herhaling(levels, staart, None if familie == GEEN else familie,
                                            st.session_state.get(sleutel_drempel, standaard)),
                            config={"displaylogo": False}, key=f"pa_herh_{rij.meetreeks_id}_{staart}")
            st.radio(f"Na T={EMPIRICAL_MAX_T}: {results.TAIL_LABELS[staart]}", [GEEN, *results.PICKABLE],
                     horizontal=True, key=sleutel,
                     format_func=lambda o: "geen keuze" if o == GEEN else results.FAMILY_LABELS[o], **opslaan)
            if opties:
                st.select_slider("Jaren boven de drempel (n_kept)", opties, key=sleutel_drempel,
                                 help="Kies waar de GPD-vorm in de drempelplot niet meer verandert. Meer jaren = "
                                      "lagere drempel: zekerder, maar minder strikt de staart.", **opslaan)
            st.text_input("Notitie", max_chars=80, key=sleutel_notitie, **opslaan)
            if oud:
                st.warning(f"Eerdere keuze **{pick.family}** kan niet meer: kies GPD of exponentieel.")
            elif pick is not None:
                st.caption(f"T ≤ {EMPIRICAL_MAX_T}: empirisch · daarna **{results.FAMILY_LABELS[pick.family]}**"
                           f"{f' met {n_kept} jaren boven de drempel' if n_kept else ''} · "
                           f"{pick.chosen_by} · {pick.chosen_at}")
            else:
                st.caption("Nog geen keuze: deze staart krijgt geen herhalingstijden in de Boxplots.")
    st.caption(f"T=1000 is extrapolatie en indicatief. Geen van deze getallen bevat de onzekerheid van het "
               "Tarso-model zelf: het is op een paar jaar metingen gekalibreerd en wordt hier als exact genomen. "
               "Die onzekerheid is waarschijnlijk groter dan het verschil tussen de families.")
    if alleen_lezen:
        st.caption("Kiezen kan hier niet opslaan (de bestanden van een Databricks App blijven niet bewaard). "
                   "Kies lokaal (`uv run streamlit run app.py`) en push `pastas/family_choice/`.")
    else:
        st.caption(f"Een keuze wordt direct opgeslagen in `{bron.keuze_pad(basis).relative_to(bron.ROOT).as_posix()}`; "
                   "push die map om haar te delen.")


PLOT_TITELS = {
    "return_periods": "Herhalingstijden: empirisch, GPD en exponentieel boven de drempel, GEV ter vergelijking",
    "threshold": "Drempelkeuze: GPD-vorm en T=1000 per drempel",
    "qq": "QQ-plot per familie",
    "confidence": "GPD en exponentieel met 95%-band",
}


def _extremenplots(basis: str, rij) -> None:
    """The figures run_extremes drew for this model, from the export in git."""
    plots = {k: bron.plot(basis, rij.series_name, k, rij.model_type) for k in PLOT_TITELS}
    if not any(plots.values()):
        st.caption("Geen extremenplots in de export voor dit model (`export_results.py` kopieert ze uit de "
                   "run-mappen naar `pastas/results_export/<prefix>/plots/`).")
        return
    hoofd = [k for k in ("return_periods", "threshold") if plots[k]] or [next(k for k, p in plots.items() if p)]
    for k in hoofd:
        st.image(str(plots[k]), caption=PLOT_TITELS[k])
    rest = [(k, p) for k, p in plots.items() if p and k not in hoofd]
    if rest:
        with st.expander("Meer plots: " + ", ".join(PLOT_TITELS[k].split(":")[0].lower() for k, _ in rest)):
            for k, p in rest:
                st.image(str(p), caption=PLOT_TITELS[k])


def _fitplots(basis: str, rij) -> None:
    """ml.plots.results() (and diagnostics) of the fit, from the export in git."""
    resultaten = bron.plot(basis, rij.series_name, "results", rij.model_type)
    diagnostiek = bron.plot(basis, rij.series_name, "diagnostics", rij.model_type)
    if resultaten is None and diagnostiek is None:
        st.caption("Geen ml.plots.results() in de export voor dit model (`export_results.py` met `results` in "
                   "`plot_kinds`).")
        return
    if resultaten is not None:
        st.image(str(resultaten), caption="ml.plots.results()")
    if diagnostiek is not None:
        with st.expander("Diagnostiek van de residuen (ml.plots.diagnostics())"):
            st.image(str(diagnostiek), caption="ml.plots.diagnostics()")


AUTOMATISCH = "automatisch"


def _bewaar_modelkeuze(basis: str, meetreeks_id: str, series_name: str, sleutel: str, sleutel_notitie: str) -> None:
    """on_change of the model pick: write it to the model_choice CSV straight away."""
    gekozen = st.session_state[sleutel]
    nieuw = selection.set_model_choice(
        _modelkeuze(basis), meetreeks_id=meetreeks_id, series_name=series_name,
        model_type=None if gekozen == AUTOMATISCH else gekozen, note=st.session_state.get(sleutel_notitie, ""),
        chosen_by=getpass.getuser(), chosen_at=datetime.now(ZoneInfo("Europe/Amsterdam")).isoformat(timespec="seconds"))
    selection.write_model_choice(bron.modelkeuze_pad(basis), nieuw)
    st.toast(f"Model: {'automatische keuze' if gekozen == AUTOMATISCH else MODEL_LABELS.get(gekozen, gekozen)} · "
             f"opgeslagen in {bron.modelkeuze_pad(basis).relative_to(bron.ROOT).as_posix()}")


def _modelvergelijking(basis: str, rij) -> None:
    """Every model type of this series side by side, and the pick of the one it uses."""
    selectie = _lees(basis, "model_selection").data
    eigen = selectie[selectie["meetreeks_id"] == rij.meetreeks_id] if not selectie.empty else selectie
    if not eigen.empty and "run_id" in eigen:
        eigen = eigen[eigen["run_id"] == eigen["run_id"].max()]
    if eigen.empty:
        st.info("Nog geen modelselectie voor deze reeks: draai de fit met meerdere `model_types` en daarna "
                "`run_extremes.py` (die schrijft `<prefix>_model_selection`).")
        return
    tabel = eigen.assign(model=eigen["model_type"].map(lambda m: MODEL_LABELS.get(m, m))).sort_values("aic")
    st.dataframe(tabel[[c for c in ("model", "selected", "passed", "pass_runs", "runaway", "n_param", "r2",
                                    "r2_holdout", "aic", "d_aic", "d_aic_tarso", "dry_T100", "wet_T100", "reason")
                        if c in tabel]], hide_index=True, width="stretch", column_config={
        "selected": st.column_config.CheckboxColumn("automatisch gekozen"),
        "passed": st.column_config.CheckboxColumn("passed", help="R² ≥ 0,7, geheugen, gain en grenzen gehaald"),
        "pass_runs": st.column_config.CheckboxColumn("runs-test", help="Autocorrelatie van de residuen; zonder "
                                                     "ruismodel vrijwel nooit gehaald, telt niet mee voor passed"),
        "runaway": st.column_config.CheckboxColumn("loopt weg", help="T=100 meer dan 1,5 m buiten het gemeten bereik"),
        "n_param": "parameters", "r2": st.column_config.NumberColumn("R²", format="%.3f"),
        "r2_holdout": st.column_config.NumberColumn("R² holdout", format="%.3f",
                                                    help="R² op de laatste 30 %, gefit op de rest"),
        "aic": st.column_config.NumberColumn("AIC", format="%.1f"),
        "d_aic": st.column_config.NumberColumn("ΔAIC", format="%+.1f", help="t.o.v. de laagste AIC van de reeks"),
        "d_aic_tarso": st.column_config.NumberColumn("ΔAIC Tarso", format="%+.1f",
                                                     help="negatief = beter dan Tarso; < −2 telt als beter"),
        "dry_T100": st.column_config.NumberColumn("T100 droog", format="%.2f"),
        "wet_T100": st.column_config.NumberColumn("T100 nat", format="%.2f"),
        "reason": "waarom"})
    st.caption("Automatische keuze: onder de modellen die de checks halen (anders alle), niet weggelopen en op de "
               "holdout niet slechter dan Tarso, de laagste AIC; een model met minder parameters binnen 2 AIC wint.")

    keuze = _modelkeuze(basis)
    eigen_keuze = keuze[keuze["meetreeks_id"] == rij.meetreeks_id]
    automatisch = eigen[eigen["selected"].astype(str).str.lower().eq("true")]["model_type"]
    opties = [AUTOMATISCH, *[m for m in DEFAULT_MODEL_TYPES if m in set(eigen["model_type"])],
              *sorted(set(eigen["model_type"]) - set(DEFAULT_MODEL_TYPES))]
    sleutel, sleutel_notitie = f"pa_model_{basis}_{rij.meetreeks_id}", f"pa_model_not_{basis}_{rij.meetreeks_id}"
    st.session_state[sleutel] = eigen_keuze["model_type"].iloc[0] if not eigen_keuze.empty and \
        eigen_keuze["model_type"].iloc[0] in opties else AUTOMATISCH
    st.session_state.setdefault(sleutel_notitie, eigen_keuze["note"].iloc[0] if not eigen_keuze.empty else "")
    opslaan = {"on_change": _bewaar_modelkeuze, "disabled": common.read_only(),
               "args": (basis, rij.meetreeks_id, rij.series_name, sleutel, sleutel_notitie)}
    auto_label = MODEL_LABELS.get(automatisch.iloc[0], automatisch.iloc[0]) if len(automatisch) else "geen"
    k1, k2 = st.columns([2, 3])
    k1.selectbox("Model voor deze reeks", opties, key=sleutel, **opslaan,
                 format_func=lambda m: f"automatisch ({auto_label})" if m == AUTOMATISCH else MODEL_LABELS.get(m, m),
                 help="Het model waarvan de herhalingstijden in de Boxplots komen.")
    k2.text_input("Notitie bij de modelkeuze", max_chars=80, key=sleutel_notitie, **opslaan)
    st.caption("Kies daarna voor dát model de familie per staart (tab Herhalingstijden, of Familie kiezen): "
               "de familiekeuze is per model.")


def _detail(basis: str, rij) -> None:
    st.subheader(_label(rij))
    if not bool(rij.success):
        st.error(f"De fit mislukte: {rij.error_message}")
        return
    m = st.columns(5)
    m[0].metric("EVP", f"{rij.evp:.1f} %")
    m[1].metric("R²", f"{rij.r2:.3f}")
    m[2].metric("RMSE", f"{rij.rmse:.3f} m")
    m[3].metric("Waarnemingen", f"{int(rij.nobs)}")
    if pd.notna(getattr(rij, "checks", None)):
        m[4].metric("Modelchecks", f"{int(rij.checks_ok)}/{int(rij.checks)} gehaald")
    st.caption(f"run `{rij.run_id}` · {rij.tmin} t/m {rij.tmax} · `{rij.meetreeks_id}`"
               + (f" · **{rij.stempel}**" if getattr(rij, "stempel", "") else "")
               + (f" · in gebruik ({rij.in_gebruik[2:]})" if getattr(rij, "in_gebruik", "") else ""))

    tab_sim, tab_model, tab_herh, tab_checks = st.tabs(
        ["Gemeten en gesimuleerd", "Modelvergelijking en keuze", "Herhalingstijden en familie", "Modelchecks"])
    with tab_model:
        _modelvergelijking(basis, rij)
    with tab_sim:
        _plot_simulatie(basis, rij)
        _fitplots(basis, rij)
    with tab_herh:
        _extremenplots(basis, rij)
        levels = _lees(basis, "return_levels", rij.meetreeks_id).data
        if levels.empty:
            st.info("Nog geen extremenanalyse voor dit model: draai `run_extremes.py` met dezelfde `data_basis` "
                    "(en, zonder live toegang, `export_results.py`).")
        else:
            levels = levels[levels["model_type"] == rij.model_type]
            _familiekeuze(basis, rij, levels)
    with tab_checks:
        checks = _lees(basis, "model_checks", rij.meetreeks_id).data
        if checks.empty:
            st.caption("Geen modelchecks voor dit model.")
        else:
            st.dataframe(checks[checks["model_type"] == rij.model_type][
                [c for c in ("check", "statistic", "operator", "threshold", "passed", "comment") if c in checks]],
                hide_index=True, width="stretch")


def render_modellen() -> None:
    st.title("Pastas-modellen")
    basis = _kies_basis("pa_basis")
    tabel = _overzicht(basis)
    if tabel is None:
        return
    meerdere = tabel.groupby("meetreeks_id")["model_type"].nunique().max() > 1
    k1, k2 = st.columns(2)
    alleen_gebruikt = k1.toggle("Alleen het model in gebruik per reeks", value=bool(meerdere), key=f"pa_gebruikt_{basis}",
                                disabled=not meerdere, help="Per reeks zijn meerdere modeltypen gefit; dit toont "
                                "alleen het model waarvan de herhalingstijden gebruikt worden.")
    alleen_passed = k2.toggle("Alleen modellen met stempel passed", key=f"pa_passed_{basis}")
    if alleen_gebruikt:
        tabel = tabel[tabel["in_gebruik"] != ""]
    if alleen_passed:
        tabel = tabel[tabel["stempel"] == "passed"]
    tabel = tabel.sort_values(["success", "r2"], ascending=[False, False], ignore_index=True)
    if tabel.empty:
        st.caption("Geen modellen met deze filters.")
        return
    labels = {f"{r.meetreeks_id}|{r.model_type}": _label(r) for r in tabel.itertuples()}
    gevraagd = st.query_params.get(MODEL_PARAM)
    gevraagd = next((k for k in labels if k.startswith(f"{gevraagd}|")), None) if gevraagd else None
    sleutel = st.selectbox("Zoek een model", list(labels), index=list(labels).index(gevraagd) if gevraagd else None,
                           format_func=labels.get, placeholder="dwarsprofiel (BE_0072…), code, locatienaam of series_…")

    kolommen = [c for c in ("profielnaam", "code", "cs_naam", "model_type", "in_gebruik", "stempel", "success", "evp",
                            "r2", "r2_holdout", "d_aic_tarso", "n_param", "rmse", "nobs", "checks_ok", "checks",
                            "familie_dry", "familie_wet", "series_name") if c in tabel]
    gekozen = st.dataframe(tabel[kolommen], hide_index=True, width="stretch", height=320, on_select="rerun",
                           selection_mode="single-row", key=f"pa_tabel_{basis}", column_config={
                               "evp": st.column_config.NumberColumn("EVP %", format="%.1f"),
                               "r2": st.column_config.NumberColumn("R²", format="%.3f"),
                               "r2_holdout": st.column_config.NumberColumn(
                                   "R² holdout", format="%.3f", help="R² op de laatste 30 %, gefit op de rest"),
                               "d_aic_tarso": st.column_config.NumberColumn(
                                   "ΔAIC Tarso", format="%+.1f", help="negatief = beter dan Tarso; < −2 telt"),
                               "n_param": "parameters",
                               "in_gebruik": st.column_config.TextColumn(
                                   "in gebruik", help="Het model waarvan de herhalingstijden in de Boxplots komen: "
                                   "automatisch gekozen, of handmatig (tab Modelvergelijking)"),
                               "stempel": st.column_config.TextColumn(
                                   "stempel", help="passed: R² ≥ 0,7, geheugen, gain en parametergrenzen gehaald "
                                   "(de runs-test telt niet mee)"),
                               "rmse": st.column_config.NumberColumn("RMSE", format="%.3f"),
                               "familie_dry": "familie droog", "familie_wet": "familie nat",
                               "profielnaam": st.column_config.TextColumn(
                                   "dwarsprofiel", help="Dwarsprofiel, zone en filter van de peilbuis "
                                   "(zoals in de Boxplots)")})
    rijen = gekozen.selection.rows if gekozen and gekozen.selection else []
    if rijen:
        r = tabel.iloc[rijen[0]]
        sleutel = f"{r['meetreeks_id']}|{r['model_type']}"
    if not sleutel:
        st.caption(f"{len(tabel)} modellen · kies er een in de tabel of zoek hierboven.")
        return
    meetreeks_id, model_type = sleutel.split("|", 1)
    st.query_params[MODEL_PARAM] = meetreeks_id
    rij = next(tabel[(tabel["meetreeks_id"] == meetreeks_id) & (tabel["model_type"] == model_type)].itertuples())
    _detail(basis, rij)


def render_vergelijken() -> None:
    st.title("Gevalideerd en ongevalideerd vergelijken")
    st.caption("Dezelfde reeks, getraind op de ruwe bron en op de goedgekeurde uren (met correctie). "
               "Een positief verschil: het gevalideerde model scoort hoger of geeft een hogere stand.")
    metrics = {b: _lees(b, "fit_metrics") for b in DATA_BASES}
    if any(m.data.empty for m in metrics.values()):
        for b, m in metrics.items():
            if m.data.empty:
                st.info(f"Nog geen modellen voor {bron.BASIS_LABEL[b].lower()} · {_herkomst(m)}")
        return
    sleutel = ["meetreeks_id", "model_type"]
    kol = ["evp", "r2", "rmse", "nobs"]
    naast = metrics[VALIDATED].data[[*sleutel, *kol]].merge(
        metrics[UNVALIDATED].data[[*sleutel, *kol]], on=sleutel, suffixes=("_gev", "_ongev"))
    for k in ("evp", "r2", "rmse"):
        naast[f"Δ {k}"] = naast[f"{k}_gev"] - naast[f"{k}_ongev"]

    T = st.segmented_control("Herhalingstijd voor de standen", [10, 100], default=10, key="pa_vgl_T") or 10
    for b, achter in ((VALIDATED, "gev"), (UNVALIDATED, "ongev")):
        lv = _lees(b, "return_levels").data
        if lv.empty:
            continue
        gekozen = results.chosen_levels(lv[lv["return_period"] == T], _keuze(b))
        breed = gekozen.pivot_table(index=sleutel, columns="tail", values="level").add_prefix(f"T{T}_").add_suffix(
            f"_{achter}").reset_index()
        naast = naast.merge(breed, on=sleutel, how="left")
    for staart in ("dry", "wet"):
        a, b = f"T{T}_{staart}_gev", f"T{T}_{staart}_ongev"
        if a in naast and b in naast:
            naast[f"Δ T{T} {staart}"] = naast[a] - naast[b]

    namen = _namen()
    naast = naast.merge(namen[[c for c in ("meetreeks_id", "profielnaam", "code", "cs_naam") if c in namen]],
                        on="meetreeks_id", how="left")
    voor = [c for c in ("profielnaam", "code", "cs_naam", "model_type") if c in naast]
    naast = naast[[*voor, *[c for c in naast if c not in voor and c != "meetreeks_id"], "meetreeks_id"]]
    st.metric("Reeksen met beide modellen", len(naast))
    st.dataframe(naast, hide_index=True, width="stretch", column_config={
        c: st.column_config.NumberColumn(c, format="%.3f") for c in naast if c.startswith(("Δ", "r2", "rmse", "T"))})
    st.download_button("Download als CSV", naast.to_csv(index=False).encode("utf-8"),
                       file_name="pastas_gevalideerd_vs_ongevalideerd.csv", mime="text/csv")
