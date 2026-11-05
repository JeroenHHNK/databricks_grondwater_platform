# app.py
"""
Streamlit-app: een kaart met alle dwarsprofielen; een klik opent de profiel- en
tijdreeksplot van dat dwarsprofiel.

Starten vanuit de repo-root:

    uv run streamlit run app.py

De verwerking zit in `modules/profiel.py`, de kaart in `modules/kaart_generator.py`.
De stijghoogtes (daggemiddelden, één gekozen meetreeks per peilbuis; zie
`modules/databricks_reeksen.py`) haalt de app bij elke nieuwe sessie live op uit
het SQL warehouse, als `DATABRICKS_WAREHOUSE_ID` is ingesteld. Anders, of als
dat mislukt, toont hij de export in `input_data/databricks_export/`.

Het gekozen dwarsprofiel staat in de URL (`?profiel=BE_0072`), zodat een link
naar een profiel te delen is.
"""

import sys
from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st
from pandas import DataFrame

# De modules printen emoji's (✅⚠️❌); zonder dit geeft een Windows-console met
# codepage cp1252 een UnicodeEncodeError midden in de verwerking.
sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

from modules import databricks_reeksen, kaart_generator, paths, workbook_lader
from modules import profiel as profiel_module
from modules.profiel import ProfielFiguren, ProjectContext

# Hoogte van de profielplot (900) plus de tijdreeksplot (500), met wat marge;
# beide figuren hebben in plotten.py een vaste afmeting.
PLOT_HOOGTE = 1450
KAART_HOOGTE = 650

# Als pagina van de centrale app (../app.py) zet die de paginaconfiguratie en
# blijft de zijbalk met de navigatie open; alleen standalone hier.
if not st.session_state.get("_centrale_app"):
    st.set_page_config(page_title="Boxplots peilbuizen", layout="wide", initial_sidebar_state="collapsed")


def _bron() -> databricks_reeksen.Bron:
    """
    De stijghoogtes van deze sessie. Eén keer per sessie opgehaald, dus elke
    keer dat iemand de app opent; navigeren binnen de app vraagt niets opnieuw.
    """
    if "bron" not in st.session_state:
        with st.spinner("Stijghoogtes ophalen uit Databricks…"):
            st.session_state["bron"] = databricks_reeksen.bron()
        st.session_state["opgehaald"] = datetime.now(ZoneInfo("Europe/Amsterdam"))
    return st.session_state["bron"]


def _versie(bron: databricks_reeksen.Bron) -> tuple[float, str]:
    """
    Cachesleutel: de mtime van config.csv en wat de stijghoogtes zijn. Live is
    dat het moment van ophalen, zodat elke sessie verse figuren krijgt; de
    export deelt zijn figuren tussen sessies.
    """
    if bron.soort == databricks_reeksen.LIVE:
        stijghoogtes = st.session_state["opgehaald"].isoformat()
    else:
        dag = databricks_reeksen.export_map() / databricks_reeksen.DAG_CSV
        stijghoogtes = f"export {dag.stat().st_mtime}"
    return paths.config_csv().stat().st_mtime, stijghoogtes


@st.cache_resource(show_spinner="Invoer laden…")
def _project_context(project: str, config_versie: float) -> ProjectContext:
    # Gedeeld tussen sessies en reruns; maak_profiel_figuren leest er alleen uit.
    return profiel_module.laad_project_context(project)


@st.cache_data(show_spinner="Dwarsprofielen laden…", max_entries=20)
def _locaties(versie: tuple[float, str], _bron: databricks_reeksen.Bron) -> DataFrame:
    """Alle hoofdprofielen van alle projecten, met lat/lon (leeg zonder coördinaten) en `heeft_data`."""
    delen: list[DataFrame] = []
    for project in paths.projecten():
        invoer = workbook_lader.laad_project(project)
        hoofd = invoer.profielen_df[invoer.profielen_df["diepte_niveau"] == workbook_lader.HOOFD]
        delen.append(
            hoofd[["profiel_id", "naam"]].assign(project=project).merge(
                kaart_generator.profiel_locaties(invoer.profielen_df, project)[["profiel_id", "lat", "lon"]],
                on="profiel_id", how="left",
            )
        )
    locaties: DataFrame = pd.concat(delen, ignore_index=True)
    met_data: set[tuple[str, str]] = databricks_reeksen.profielen_met_data(_bron)
    locaties["heeft_data"] = [(p, k) in met_data for p, k in zip(locaties["project"], locaties["profiel_id"])]
    return locaties.sort_values(["project", "profiel_id"], ignore_index=True)


@st.cache_resource(show_spinner="Stijghoogtes klaarzetten…", max_entries=50)
def _reeksen(project: str, versie: tuple[float, str], _bron: databricks_reeksen.Bron) -> profiel_module.LaadReeksen:
    return databricks_reeksen.laad_reeksen(_bron, project)


@st.cache_data(show_spinner="Profiel verwerken…", max_entries=200)
def _figuren(project: str, profiel_id: str, versie: tuple[float, str], _bron: databricks_reeksen.Bron) -> ProfielFiguren:
    return profiel_module.maak_profiel_figuren(
        _project_context(project, versie[0]), profiel_id, _reeksen(project, versie, _bron)
    )


def _herkomst(bron: databricks_reeksen.Bron) -> str:
    """Waar de stijghoogtes vandaan komen, voor in een bijschrift."""
    if bron.soort == databricks_reeksen.LIVE:
        return (f"stijghoogtes live uit Databricks t/m {bron.laatste_meting()} "
                f"(opgehaald {st.session_state['opgehaald']:%H:%M})")
    return f"stijghoogtes uit de export t/m {bron.laatste_meting()}"


def _open_profiel(profiel_id: str | None) -> None:
    """Naar een profiel, of met None terug naar de kaart."""
    if profiel_id is None:
        st.query_params.clear()
    else:
        st.query_params["profiel"] = profiel_id
    # Een nieuwe sleutel voor kaart en zoekveld, zodat een oude selectie niet
    # meteen opnieuw een profiel opent.
    st.session_state["kaart_versie"] = st.session_state.get("kaart_versie", 0) + 1
    st.rerun()


def _gekozen_op_kaart(gebeurtenis) -> str | None:
    punten = (gebeurtenis or {}).get("selection", {}).get("points", [])
    for punt in punten:
        data = punt.get("customdata")
        if data is not None and len(data) == 2:
            return str(data[1])
    return None


def _toon_kaart(locaties: DataFrame, bron: databricks_reeksen.Bron) -> None:
    st.title("Boxplots peilbuizen")
    zonder_data: int = int((~locaties["heeft_data"]).sum())
    kop, knop = st.columns([5, 1], vertical_alignment="bottom")
    kop.caption(
        f"{len(locaties)} dwarsprofielen in {locaties['project'].nunique()} projecten · "
        f"{_herkomst(bron)} · klik op een dwarsprofiel om het te openen"
        + (f" · grijs: {zonder_data} zonder stijghoogtes" if zonder_data else "")
    )
    if knop.button("↻ Ververs stijghoogtes", width="stretch"):
        del st.session_state["bron"]
        st.rerun()
    nr: int = st.session_state.get("kaart_versie", 0)
    op_kaart: DataFrame = locaties.dropna(subset=["lat", "lon"])
    gebeurtenis = st.plotly_chart(
        kaart_generator.maak_overzichtskaart(op_kaart, KAART_HOOGTE),
        on_select="rerun",
        selection_mode="points",
        key=f"kaart_{nr}",
        config={"scrollZoom": True, "displaylogo": False},
    )
    gekozen: str | None = _gekozen_op_kaart(gebeurtenis)

    labels: dict[str, str] = {
        pid: f"{naam} · {project}" + ("" if data else " (geen stijghoogtes)")
        for pid, naam, project, data in locaties[["profiel_id", "naam", "project", "heeft_data"]].itertuples(index=False)
    }
    gezocht = st.selectbox(
        "Of zoek een dwarsprofiel",
        list(labels),
        index=None,
        format_func=labels.get,
        placeholder="BE_0072, PU_0110, …",
        key=f"zoek_{nr}",
    )
    zonder_coordinaten = locaties[locaties["lat"].isna()]
    if not zonder_coordinaten.empty:
        st.caption(f"Niet op de kaart (geen coördinaten): {', '.join(zonder_coordinaten['naam'])}")
    if gekozen or gezocht:
        _open_profiel(gekozen or gezocht)


def _toon_invoercontrole(ctx: ProjectContext) -> None:
    bevindingen = workbook_lader.valideer(ctx.invoer)
    if not bevindingen:
        return
    with st.expander(f"Invoercontrole ({len(bevindingen)})"):
        for bevinding in bevindingen:
            st.caption(f"{'❌' if bevinding.ernst == 'fout' else '⚠️'} {bevinding}")


def _toon_profiel(rij: pd.Series, versie: tuple[float, str], bron: databricks_reeksen.Bron) -> None:
    project: str = rij["project"]
    profiel_id: str = rij["profiel_id"]
    if st.button("← Terug naar de kaart"):
        _open_profiel(None)

    try:
        ctx: ProjectContext = _project_context(project, versie[0])
    except Exception as fout:  # noqa: BLE001 - de app moet blijven draaien
        st.error(f"Project {project} kon niet geladen worden: {fout}")
        return
    profiel: pd.Series = ctx.invoer.profielen_df.set_index("profiel_id").loc[profiel_id]
    diep: list[str] = ctx.invoer.profielen_df.loc[
        ctx.invoer.profielen_df["hoofdprofiel_id"] == profiel_id, "naam"
    ].tolist()

    st.header(f"{profiel['naam']} · {project}")
    st.caption(" · ".join(deel for deel in (
        f"stix `{profiel['stix_bestand']}`" if profiel["stix_bestand"] else "geen stix",
        f"diepere peilbuizen: {', '.join(diep)}" if diep else "",
        _herkomst(bron),
    ) if deel))
    _toon_invoercontrole(ctx)

    tab_plot, tab_peilbuizen = st.tabs(["Profiel", "Peilbuizen"])
    with tab_plot:
        try:
            figuren = _figuren(project, profiel_id, versie, bron)
        except profiel_module.GeenStijghoogtes as fout:
            st.info(f"{fout} Zie input_data/controle/te_controleren.csv.")
            figuren = None
        except Exception as fout:  # noqa: BLE001
            st.error(f"Profiel {profiel['naam']} kon niet verwerkt worden: {fout}")
            st.exception(fout)
            figuren = None
        if figuren is not None:
            if figuren.ontbrekend:
                st.warning(f"Geen stijghoogtes voor: {', '.join(figuren.ontbrekend)}. Die staan niet in de plot.")
            html: str = figuren.als_html()
            st.download_button(
                "Download als HTML",
                data=html,
                file_name=f"plot_{project}_{figuren.uitvoer_stam}.html",
                mime="text/html",
            )
            # Eén iframe in plaats van twee keer st.plotly_chart: zo blijft de
            # tijdslider van de profielplot gekoppeld aan de markering in de tijdreeks.
            # De HTML komt uit onze eigen code, niet van een gebruiker.
            st.iframe(html, height=PLOT_HOOGTE)
    with tab_peilbuizen:
        profiel_ids: set[str] = {profiel_id} | set(
            ctx.invoer.profielen_df.loc[ctx.invoer.profielen_df["hoofdprofiel_id"] == profiel_id, "profiel_id"]
        )
        peilbuizen: DataFrame = ctx.invoer.peilbuizen_df
        st.dataframe(peilbuizen[peilbuizen["profiel_id"].isin(profiel_ids)], hide_index=True, width="stretch")


def main() -> None:
    try:
        bron: databricks_reeksen.Bron = _bron()
    except Exception as fout:  # noqa: BLE001 - live mislukt en er is geen export
        st.error(f"Geen stijghoogtes: live ophalen mislukte ({fout}) en er is geen export in "
                 f"{databricks_reeksen.export_map()}.")
        return
    if bron.fout:
        st.warning(f"Live ophalen uit Databricks mislukte, dus de app toont de export. ({bron.fout})")
    versie = _versie(bron)
    locaties: DataFrame = _locaties(versie, bron)

    gevraagd: str | None = st.query_params.get("profiel")
    rij: DataFrame = locaties[locaties["profiel_id"] == gevraagd]
    if gevraagd and rij.empty:
        st.warning(f"Dwarsprofiel {gevraagd} bestaat niet.")
    if rij.empty:
        _toon_kaart(locaties, bron)
    else:
        _toon_profiel(rij.iloc[0], versie, bron)


main()
