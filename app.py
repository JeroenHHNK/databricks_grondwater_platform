# app.py
"""
Streamlit-app: de profiel- en tijdreeksplots per project, zonder HTML-bestanden.

Starten vanuit de repo-root:

    uv run streamlit run app.py

De verwerking is dezelfde als in `main.py` (`Modules/profiel.py`); alleen de
uitvoer verschilt. De invoer is de csv in `input_data/<project>/`; elk
dwarsprofiel heet naar zijn dijkpaal (`BE_0072`).

De stijghoogtes komen uit een van deze bronnen; zijn er meer, dan is de bron in
de zijbalk te kiezen en staat Databricks bovenaan:
1. de Databricks-export in `input_data/databricks_export/` (daggemiddelden, één
   gekozen meetreeks per peilbuis; zie `Modules/databricks_reeksen.py`);
2. `peilbuisdata/<peilbuis_naam>.csv` (scripts/zet_export_om.py);
3. de peilbuisdata-export (xlsx) in `peilbuisdata/`;
4. een upload van die export, die alleen in het geheugen blijft.
"""

import io
import sys
from pathlib import Path

import pandas as pd
import streamlit as st
from pandas import DataFrame

# De modules printen emoji's (✅⚠️❌); zonder dit geeft een Windows-console met
# codepage cp1252 een UnicodeEncodeError midden in de verwerking. Zie main.py.
sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

from Modules import data_lader, databricks_reeksen, kaart_generator, paths, workbook_lader
from Modules import profiel as profiel_module
from Modules.profiel import LaadReeksen, ProfielFiguren, ProjectContext

# Hoogte van de profielplot (900) plus de tijdreeksplot (500), met wat marge;
# beide figuren hebben in plotten.py een vaste afmeting.
PLOT_HOOGTE = 1450
KAART_HOOGTE = 550

st.set_page_config(page_title="Boxplots peilbuizen", layout="wide")


@st.cache_resource(show_spinner="Werkboek laden…")
def _project_context(project: str) -> ProjectContext:
    # Gedeeld tussen sessies en reruns; maak_profiel_figuren leest er alleen uit.
    return profiel_module.laad_project_context(project)


@st.cache_data(show_spinner=False)
def _reeksen_uit_bestand(pad: str, gewijzigd: float, namen: tuple[str, ...]) -> DataFrame:
    # `gewijzigd` zit alleen in de cachesleutel: een nieuwe export wordt opnieuw gelezen.
    return data_lader.laad_peilbuis_data(Path(pad), list(namen))


@st.cache_data(show_spinner=False)
def _reeksen_uit_csv(map_pad: str, namen: tuple[str, ...], gewijzigd: tuple[float, ...]) -> DataFrame:
    # `gewijzigd` (mtime per csv) zit alleen in de cachesleutel.
    return data_lader.laad_peilbuis_csv(Path(map_pad), list(namen))


def _mtimes(map_pad: Path, namen: list[str]) -> tuple[float, ...]:
    return tuple(
        (map_pad / f"{n}.csv").stat().st_mtime if (map_pad / f"{n}.csv").is_file() else 0.0
        for n in namen
    )


@st.cache_resource(show_spinner="Stijghoogtes uit Databricks laden…")
def _databricks_lader(project: str, gewijzigd: float, config_gewijzigd: float) -> LaadReeksen:
    # De mtimes van de export en van config.csv zitten alleen in de cachesleutel.
    return databricks_reeksen.export_reeksen(project)


@st.cache_data(show_spinner=False)
def _reeksen_uit_upload(inhoud: bytes, namen: tuple[str, ...]) -> DataFrame:
    return data_lader.laad_peilbuis_data(io.BytesIO(inhoud), list(namen))


@st.cache_data(show_spinner="Profiel verwerken…")
def _figuren(
    project: str, profiel_id: str, bron_sleutel: str, _laad_reeksen: LaadReeksen
) -> ProfielFiguren:
    # `_laad_reeksen` wordt niet gehasht (de underscore); `bron_sleutel` staat
    # ervoor in, zodat een andere bron een andere cache-ingang geeft.
    return profiel_module.maak_profiel_figuren(
        _project_context(project), profiel_id, _laad_reeksen
    )


def _bronnen(ctx: ProjectContext) -> list[tuple[str, str, LaadReeksen]]:
    """De beschikbare stijghoogtebronnen als (label, cachesleutel, lader), Databricks eerst."""
    bronnen: list[tuple[str, str, LaadReeksen]] = []
    if databricks_reeksen.export_beschikbaar():
        dag: Path = databricks_reeksen.export_map() / databricks_reeksen.DAG_CSV
        gewijzigd: float = dag.stat().st_mtime
        config_gewijzigd: float = paths.config_csv().stat().st_mtime
        bronnen.append((
            "Databricks (daggemiddelden)",
            f"databricks:{gewijzigd}:{config_gewijzigd}",
            lambda namen: _databricks_lader(ctx.project, gewijzigd, config_gewijzigd)(namen),
        ))

    reeksen: Path | None = ctx.invoer.paden.get("reeksen")
    if reeksen is not None:
        bronnen.append((
            f"lokaal: peilbuisdata/ ({len(list(reeksen.glob('*.csv')))} csv's)",
            f"csv:{reeksen}",
            lambda namen: _reeksen_uit_csv(str(reeksen), tuple(namen), _mtimes(reeksen, namen)),
        ))

    pad: Path | None = ctx.invoer.paden["peilbuisdata"]
    if pad is not None:
        bestand_gewijzigd: float = pad.stat().st_mtime
        bronnen.append((
            f"lokaal: {pad.name}",
            f"bestand:{pad}:{bestand_gewijzigd}",
            lambda namen: _reeksen_uit_bestand(str(pad), bestand_gewijzigd, tuple(namen)),
        ))
    return bronnen


def _kies_bron(ctx: ProjectContext) -> tuple[str, LaadReeksen] | None:
    """De stijghoogtebron: Databricks, een lokale bron, of een upload. None zolang er geen is."""
    bronnen = _bronnen(ctx)
    if bronnen:
        labels: list[str] = [label for label, _, _ in bronnen]
        if len(bronnen) == 1:
            st.sidebar.caption(f"Stijghoogtes: {labels[0]}")
            gekozen = bronnen[0]
        else:
            gekozen = bronnen[labels.index(st.sidebar.radio("Stijghoogtes", labels))]
        return gekozen[1], gekozen[2]

    verwacht: str = str(ctx.invoer.algemene_config.get("peilbuisdata_bestand", ""))
    upload = st.sidebar.file_uploader(
        "Peilbuisdata-export (.xlsx)",
        type=["xlsx"],
        help=f"Geen Databricks-export en niets in input_data/{ctx.project}/peilbuisdata/. "
        f"Verwacht: {verwacht}",
    )
    if upload is None:
        return None
    inhoud: bytes = upload.getvalue()
    return (
        f"upload:{upload.name}:{upload.file_id}",
        lambda namen: _reeksen_uit_upload(inhoud, tuple(namen)),
    )


def _toon_werkboekcontrole(ctx: ProjectContext) -> None:
    bevindingen = workbook_lader.valideer(ctx.invoer)
    if not bevindingen:
        return
    with st.sidebar.expander(f"Werkboekcontrole ({len(bevindingen)})"):
        for bevinding in bevindingen:
            st.caption(f"{'❌' if bevinding.ernst == 'fout' else '⚠️'} {bevinding}")


def _toon_profiel(ctx: ProjectContext, profiel: pd.Series, bron: tuple[str, LaadReeksen]) -> None:
    bron_sleutel, laad_reeksen = bron
    try:
        figuren = _figuren(ctx.project, profiel["profiel_id"], bron_sleutel, laad_reeksen)
    except profiel_module.GeenStijghoogtes as fout:
        st.info(f"{fout} Zie input_data/databricks_export/te_controleren.csv.")
        return
    except Exception as fout:  # noqa: BLE001 - de app moet blijven draaien
        st.error(f"Profiel {profiel['naam']} kon niet verwerkt worden: {fout}")
        st.exception(fout)
        return

    if figuren.ontbrekend:
        st.warning(f"Geen stijghoogtes voor: {', '.join(figuren.ontbrekend)}. Die staan niet in de plot.")
    html: str = figuren.als_html()
    st.download_button(
        "Download als HTML",
        data=html,
        file_name=f"plot_{ctx.project}_{figuren.uitvoer_stam}.html",
        mime="text/html",
    )
    # Eén iframe in plaats van twee keer st.plotly_chart: zo blijft de
    # tijdslider van de profielplot gekoppeld aan de markering in de tijdreeks.
    # De HTML komt uit onze eigen code, niet van een gebruiker.
    st.iframe(html, height=PLOT_HOOGTE)


def _toon_kaart(ctx: ProjectContext) -> None:
    kaart = kaart_generator.maak_project_kaart(
        ctx.invoer.profielen_df,
        sorted(paths.stix_map().glob("*.stix")),
        ctx.project,
    )
    if kaart is None:
        st.info("Geen profielen met coördinaten in dit project.")
        return
    st.iframe(kaart.get_root().render(), height=KAART_HOOGTE)


def main() -> None:
    st.sidebar.title("Boxplots peilbuizen")
    projecten: list[str] = paths.projecten()
    if not projecten:
        st.error(f"Geen projecten gevonden in {paths.invoer_basis()}.")
        return
    project: str = st.sidebar.selectbox("Project", projecten)

    try:
        ctx: ProjectContext = _project_context(project)
    except Exception as fout:  # noqa: BLE001
        st.error(f"Project {project} kon niet geladen worden: {fout}")
        return
    _toon_werkboekcontrole(ctx)

    profielen: DataFrame = ctx.hoofdprofielen()
    profielen = profielen[profielen["actief"]]
    if profielen.empty:
        st.warning("Dit project heeft geen actieve hoofdprofielen.")
        return
    profiel_id: str = st.sidebar.selectbox(
        "Dwarsprofiel",
        profielen["profiel_id"].tolist(),
        format_func=lambda pid: profielen.loc[profielen["profiel_id"] == pid, "naam"].iloc[0],
    )
    profiel: pd.Series = profielen[profielen["profiel_id"] == profiel_id].iloc[0]

    bron = _kies_bron(ctx)

    st.header(f"{project} · {profiel['naam']}")
    diep: list[str] = ctx.invoer.profielen_df.loc[
        ctx.invoer.profielen_df["hoofdprofiel_id"] == profiel_id, "naam"
    ].tolist()
    st.caption(
        " · ".join(
            deel
            for deel in (
                f"stix `{profiel['stix_bestand']}`" if profiel["stix_bestand"] else "geen stix",
                f"diepere peilbuizen: {', '.join(diep)}" if diep else "",
            )
            if deel
        )
    )
    tab_plot, tab_kaart, tab_peilbuizen = st.tabs(["Profiel", "Kaart", "Peilbuizen"])
    with tab_plot:
        if bron is None:
            st.info(
                "Geen stijghoogtes gevonden voor dit project: geen Databricks-export, geen csv's in "
                f"`input_data/{project}/peilbuisdata/` en geen export. Upload de export "
                "in de zijbalk (die blijft alleen in het geheugen), of zet hem om met "
                f"`uv run python scripts/zet_export_om.py --project {project}`."
            )
        else:
            _toon_profiel(ctx, profiel, bron)
    with tab_kaart:
        _toon_kaart(ctx)
    with tab_peilbuizen:
        profiel_ids: set[str] = {profiel_id} | set(
            ctx.invoer.profielen_df.loc[
                ctx.invoer.profielen_df["hoofdprofiel_id"] == profiel_id, "profiel_id"
            ]
        )
        peilbuizen: DataFrame = ctx.invoer.peilbuizen_df
        st.dataframe(
            peilbuizen[peilbuizen["profiel_id"].isin(profiel_ids)],
            hide_index=True,
            width="stretch",
        )


main()
