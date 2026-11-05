# profiel.py
"""
De verwerking van één profiel: van werkboek en meetreeksen naar twee Plotly-figuren.

`maak_profiel_figuren` geeft de figuren terug, zodat `app.py` ze in Streamlit
toont en `notebooks/maak_plots.py` ze als HTML wegschrijft.

Waar de stijghoogtes vandaan komen is een parameter (`LaadReeksen`); nu is dat
Databricks, live of uit de export (`databricks_reeksen.laad_reeksen`). De rest
van de verwerking merkt daar niets van.

Een peilbuis zonder stijghoogtes valt uit zijn dwarsprofiel, in plaats van het
hele profiel te laten mislukken; `ProfielFiguren.ontbrekend` zegt welke.
"""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field
from typing import Any, Callable

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from pandas import DataFrame, Series

from . import analyse, data_lader, plotten, stix_verwerker, workbook_lader
from .workbook_lader import ProjectInvoer

# Krijgt de peilbuisnamen (`stijghoogte_namen`) en geeft een DataFrame terug met
# een DatetimeIndex en één kolom per naam.
# Een naam zonder data mag ontbreken of alleen NaN bevatten.
LaadReeksen = Callable[[list[str]], DataFrame]

BUITENWATERSTAND_KOLOM = "Buitenwaterstand Markermeer"


class GeenStijghoogtes(ValueError):
    """Geen enkele peilbuis van het hoofdprofiel heeft stijghoogtes: er valt niets te plotten."""


@dataclass
class ProjectContext:
    """Alles wat per project één keer geladen wordt en voor elk profiel gelijk is."""

    invoer: ProjectInvoer
    neerslag_data: DataFrame
    # Een DataFrame met kolom 'waterstand' (fluctuerend) of een vast getal.
    buitenwaterstand_data: DataFrame | float

    @property
    def project(self) -> str:
        return self.invoer.project

    def hoofdprofielen(self) -> DataFrame:
        """De profielen die een eigen plot krijgen; A/B-profielen vallen eronder."""
        df: DataFrame = self.invoer.profielen_df
        return df[df["diepte_niveau"] == "hoofd"]


@dataclass
class ProfielFiguren:
    profiel_naam: str
    uitvoer_stam: str
    profiel_figuur: go.Figure
    tijdreeks_figuur: go.Figure
    # Peilbuizen van dit dwarsprofiel (ook A/B) zonder stijghoogtes; die staan niet in de plot.
    ontbrekend: list[str] = field(default_factory=list)

    def als_html(self) -> str:
        """De twee figuren plus het script dat de tijdslider aan de tijdreeks koppelt."""
        return (
            self.profiel_figuur.to_html(
                full_html=False,
                include_plotlyjs="cdn",
                div_id=plotten.PROFIEL_PLOT_DIV_ID,
            )
            + self.tijdreeks_figuur.to_html(
                full_html=False,
                include_plotlyjs=False,
                div_id=plotten.TIJDREEKS_PLOT_DIV_ID,
            )
            + plotten.SLIDER_SYNC_SCRIPT
        )


def parseer_x_coordinaten(waarde: Any) -> list[float]:
    """
    Parset een string van door slashes gescheiden coördinaten met komma's als decimaalteken
    naar een lijst van floats. Voorbeeld: '25,6/50,2' -> [25.6, 50.2].
    Gaat correct om met invoer die geen string is.
    """
    if pd.isna(waarde):
        return []

    waarde_str = str(waarde)

    if not waarde_str.strip():
        return []

    delen = waarde_str.strip().split("/")
    coordinaten = []
    for deel in delen:
        if not deel.strip():
            continue
        try:
            # Vervang komma door punt voor float-conversie en verwijder witruimte
            float_waarde = float(deel.strip().replace(",", "."))
            coordinaten.append(float_waarde)
        except (ValueError, TypeError):
            # Behandel gevallen waar een deel geen geldig getal is
            print(f"⚠️ Kon x-coordinaat niet parsen: '{deel}'. Wordt overgeslagen.")
            continue

    return coordinaten


def laad_project_context(project: str) -> ProjectContext:
    """Leest en valideert het werkboek en laadt de invoer die alle profielen delen."""
    invoer: ProjectInvoer = workbook_lader.laad_project(project)

    bevindingen = workbook_lader.valideer(invoer)
    for bevinding in bevindingen:
        print(f"{'❌' if bevinding.ernst == 'fout' else '⚠️'} {bevinding}")
    fouten = [b for b in bevindingen if b.ernst == "fout"]
    if fouten:
        raise ValueError(
            f"{len(fouten)} fout(en) in {invoer.workbook.name}; los die eerst op."
        )

    return ProjectContext(
        invoer=invoer,
        neerslag_data=data_lader.laad_neerslag_data(invoer.paden["neerslag"]),
        buitenwaterstand_data=_laad_buitenwaterstand(invoer),
    )


def _met_data(pb_gegevens: DataFrame, namen: list[str]) -> list[bool]:
    """Per naam: zit er minstens één meting in `pb_gegevens`?"""
    return [n in pb_gegevens.columns and bool(pb_gegevens[n].notna().any()) for n in namen]


def _laad_buitenwaterstand(invoer: ProjectInvoer) -> DataFrame | float:
    """Fluctuerende buitenwaterstand uit een bestand, anders de vaste waarde uit het werkboek."""
    algemene_config: dict = invoer.algemene_config
    bestand_pad = invoer.paden["buitenwaterstand"]

    if not bestand_pad:
        print("Vaste buitenwaterstand wordt gebruikt uit general_config.")
        return algemene_config["buitenwaterstand"]

    try:
        print(f"Fluctuerende buitenwaterstand wordt ingelezen uit: {bestand_pad}")
        df_bw = data_lader.lees_tabel(bestand_pad)

        # Zoek naar datum- en waterstandkolommen (flexibel)
        datum_kolom = next((col for col in df_bw.columns if 'datum' in col.lower() or 'date' in col.lower()), None)
        waterstand_kolom = next((col for col in df_bw.columns if 'waterstand' in col.lower() or 'niveau' in col.lower()), None)

        if datum_kolom and waterstand_kolom:
            df_bw = df_bw.rename(columns={datum_kolom: 'datum', waterstand_kolom: 'waterstand'})
            df_bw['datum'] = pd.to_datetime(df_bw['datum'])
            df_bw = df_bw.set_index('datum')
            print("✅ Fluctuerende buitenwaterstand succesvol geladen.")
            return df_bw[['waterstand']]
        print(f"⚠️ WAARSCHUWING: Kon 'datum' of 'waterstand' kolom niet vinden in {bestand_pad}. Fallback naar vaste waterstand.")
    except FileNotFoundError:
        print(f"⚠️ WAARSCHUWING: Bestand niet gevonden: {bestand_pad}. Fallback naar vaste waterstand.")
    except Exception as e:
        print(f"❌ Fout bij lezen van buitenwaterstand-bestand: {e}. Fallback naar vaste waterstand.")
    return algemene_config["buitenwaterstand"]


@dataclass
class _DiepProfiel:
    """Een 'A'- of 'B'-profiel: diepere peilbuizen onder een hoofdprofiel."""

    profiel: Series
    metadata_pb: DataFrame
    x_peilbuis: list[float]
    pb_gegevens: DataFrame
    stijghoogtes: list[np.ndarray]
    ontbrekend: list[str] = field(default_factory=list)

    def metadata_zonder_labels(self) -> DataFrame:
        # De labels van de diepere peilbuizen zouden die van het hoofdprofiel
        # overschrijven of ermee conflicteren in de plot; alleen het hoofdprofiel
        # krijgt labels.
        metadata = self.metadata_pb.copy()
        metadata["Code_peilbuis"] = [""] * len(metadata)
        return metadata


def _laad_diep_profiel(
    ctx: ProjectContext,
    profiel: Series,
    diepte_niveau: str,
    omschrijving: str,
    offset_x: float,
    laad_reeksen: LaadReeksen,
) -> _DiepProfiel | None:
    profielen_df: DataFrame = ctx.invoer.profielen_df
    rij: DataFrame = profielen_df[
        (profielen_df["hoofdprofiel_id"] == profiel["profiel_id"])
        & (profielen_df["diepte_niveau"] == diepte_niveau)
    ]
    if rij.empty:
        return None
    naam: str = rij.iloc[0]["naam"]

    print(f"✅ {omschrijving} gevonden: {naam}")
    diep_profiel: Series = rij.iloc[0]
    try:
        metadata_pb = workbook_lader.metadata_voor_profiel(
            ctx.invoer, diep_profiel["profiel_id"]
        )
        x_peilbuis = [
            x + offset_x
            for x in parseer_x_coordinaten(diep_profiel["x_coordinaten_dwarsprofiel"])
        ]
        stijghoogte_namen = str(diep_profiel["stijghoogte_namen"]).split(",")
        pb_gegevens = laad_reeksen(stijghoogte_namen)
        aanwezig = _met_data(pb_gegevens, stijghoogte_namen)
        ontbrekend = [n for n, a in zip(stijghoogte_namen, aanwezig) if not a]
        if ontbrekend:
            print(f"⚠️ {naam}: geen stijghoogtes voor {', '.join(ontbrekend)}; die vallen weg.")
        if not any(aanwezig):
            return _DiepProfiel(diep_profiel, metadata_pb.iloc[0:0], [], DataFrame(), [], ontbrekend)
        stijghoogte_namen = [n for n, a in zip(stijghoogte_namen, aanwezig) if a]
        x_peilbuis = [x for x, a in zip(x_peilbuis, aanwezig) if a]
        metadata_pb = metadata_pb[aanwezig].reset_index(drop=True)
        pb_gegevens = pb_gegevens[stijghoogte_namen]
        stijghoogtes = [pb_gegevens[n].dropna().to_numpy() for n in stijghoogte_namen]
    except Exception as e:
        print(f"❌ Fout bij laden van {omschrijving.lower()} data: {e}")
        return None
    return _DiepProfiel(diep_profiel, metadata_pb, x_peilbuis, pb_gegevens, stijghoogtes, ontbrekend)


def maak_profiel_figuren(
    ctx: ProjectContext, profiel_id: str, laad_reeksen: LaadReeksen
) -> ProfielFiguren:
    """De profielplot en de tijdreeksplot van één hoofdprofiel."""
    invoer: ProjectInvoer = ctx.invoer
    algemene_config: dict = invoer.algemene_config
    rij: DataFrame = invoer.profielen_df[invoer.profielen_df["profiel_id"] == profiel_id]
    if rij.empty:
        raise KeyError(f"Profiel {profiel_id} staat niet in {invoer.workbook.name}.")
    profiel: Series = rij.iloc[0]
    profiel_naam: str = profiel["naam"]

    # --- Dwarsprofielgeometrie uit het werkboek ---
    coords_df = workbook_lader.laad_coords(invoer, profiel_id)
    dijkprofiel = coords_df[["x", "y"]].to_numpy()

    # --- STIX-bestand en eventuele horizontale correctie ---
    # De STIX-geometrie (grondopbouw) is leidend qua ligging en wordt zelf niet
    # verplaatst. Het Excel-dijkprofiel (en alles wat daaruit wordt afgeleid, zoals
    # peilbuisposities en de freatische lijn) wordt zo nodig horizontaal verschoven
    # zodat het aansluit op de STIX-geometrie.
    stix_bestand = workbook_lader.stix_pad(invoer, profiel_id)
    ruwe_geometrie_per_grondsoort = {}
    offset_x = 0.0

    if stix_bestand:
        print(f"✅ Stix-bestand gevonden: {stix_bestand.name}")
        ruwe_geometrie_per_grondsoort = stix_verwerker.verwerk_stix_bestand(stix_bestand)

        handmatige_offset = profiel.get("stix_offset_x_handmatig")
        if pd.notna(handmatige_offset):
            offset_x = float(str(handmatige_offset).replace(",", "."))
            print(
                f"   -> Handmatige x-correctie dijkprofiel t.o.v. STIX: dx={offset_x:.3f}m"
            )
        elif ruwe_geometrie_per_grondsoort:
            offset_x, rmse_met, rmse_zonder = stix_verwerker.bepaal_horizontale_offset(
                dijkprofiel, ruwe_geometrie_per_grondsoort
            )
            if abs(offset_x) > 0.01:
                print(
                    f"   -> Automatische x-correctie dijkprofiel t.o.v. STIX: dx={offset_x:.3f}m "
                    f"(afwijking {rmse_zonder:.3f}m -> {rmse_met:.3f}m)"
                )
            if rmse_met > 0.5:
                if rmse_zonder - rmse_met > 0.05:
                    advies = ("een verschuiving helpt deels; zet zo nodig zelf "
                              "'stix_offset_x' in profielen.csv.")
                else:
                    advies = ("verschuiven helpt niet: stix en dijkprofiel verschillen in "
                              "hoogte, niet in ligging. Geen offset nodig.")
                print(
                    "   ⚠️ Resterende afwijking tussen dijkprofiel en stix-maaiveld "
                    f"(RMSE {rmse_met:.2f}m); {advies}"
                )

        dijkprofiel[:, 0] += offset_x
    else:
        print(
            f"⚠️ Geen stix-bestand gevonden voor profiel '{profiel_naam}'. Plot wordt zonder grondopbouw gemaakt."
        )

    # --- Diepe ('A') en nog diepere ('B') peilbuizen ---
    diep_a = _laad_diep_profiel(ctx, profiel, "A", "Diep profiel", offset_x, laad_reeksen)
    diep_b = _laad_diep_profiel(ctx, profiel, "B", "Nog dieper profiel", offset_x, laad_reeksen)
    ontbrekend_diep: list[str] = [n for d in (diep_a, diep_b) if d is not None for n in d.ontbrekend]
    # Een A/B-profiel waarvan geen enkele peilbuis data heeft, telt niet mee.
    diep_a = diep_a if diep_a is not None and diep_a.x_peilbuis else None
    diep_b = diep_b if diep_b is not None and diep_b.x_peilbuis else None

    # De STIX-geometrie blijft ongewijzigd (leidend); alleen kleuren omzetten.
    uitvoer_stam = stix_bestand.stem if stix_bestand else profiel_naam.replace(" ", "_")
    geometrie_per_grondsoort = {
        grondsoort: {
            "color": stix_verwerker.argb_naar_rgb(waarde["color"]),
            "polygons": waarde["polygons"],
        }
        for grondsoort, waarde in ruwe_geometrie_per_grondsoort.items()
    }

    metadata_pb = workbook_lader.metadata_voor_profiel(invoer, profiel_id)

    x_peilbuis = [
        x + offset_x
        for x in parseer_x_coordinaten(profiel["x_coordinaten_dwarsprofiel"])
    ]

    stijghoogte_namen = str(profiel["stijghoogte_namen"]).split(",")
    pb_gegevens: DataFrame = laad_reeksen(stijghoogte_namen)
    aanwezig: list[bool] = _met_data(pb_gegevens, stijghoogte_namen)
    ontbrekend: list[str] = [n for n, a in zip(stijghoogte_namen, aanwezig) if not a]
    if not any(aanwezig):
        raise GeenStijghoogtes(
            f"Geen stijghoogtes voor de peilbuizen van {profiel_naam} ({', '.join(stijghoogte_namen)})."
        )
    if ontbrekend:
        print(f"⚠️ {profiel_naam}: geen stijghoogtes voor {', '.join(ontbrekend)}; die vallen weg.")
        stijghoogte_namen = [n for n, a in zip(stijghoogte_namen, aanwezig) if a]
        x_peilbuis = [x for x, a in zip(x_peilbuis, aanwezig) if a]
        metadata_pb = metadata_pb[aanwezig].reset_index(drop=True)
        pb_gegevens = pb_gegevens[stijghoogte_namen]
    y_peilbuis_mv = [
        analyse.vind_y_in_dwarsprofiel(dijkprofiel, x) for x in x_peilbuis
    ]
    stijghoogtes = [
        pb_gegevens[naam].dropna().to_numpy() for naam in stijghoogte_namen
    ]
    gemiddelden = [np.mean(data) for data in stijghoogtes if len(data) > 0]

    # --- Combineer alle peilbuisdata en integreer buitenwaterstand ---
    ts_pb_gegevens = pb_gegevens.copy()
    for diep in (diep_a, diep_b):
        if diep is not None:
            ts_pb_gegevens = pd.concat([ts_pb_gegevens, diep.pb_gegevens], axis=1)

    buitenwaterstand_data = ctx.buitenwaterstand_data
    if isinstance(buitenwaterstand_data, pd.DataFrame):
        ts_pb_gegevens = ts_pb_gegevens.sort_index()
        bw_df_sorted = buitenwaterstand_data.sort_index()

        # Voeg de waterstand toe aan de gecombineerde dataset
        ts_pb_gegevens = pd.merge_asof(
            ts_pb_gegevens,
            bw_df_sorted.rename(columns={'waterstand': BUITENWATERSTAND_KOLOM}),
            left_index=True,
            right_index=True,
            direction='nearest'
        )
        if BUITENWATERSTAND_KOLOM in ts_pb_gegevens.columns and not ts_pb_gegevens[BUITENWATERSTAND_KOLOM].empty:
            buitenwaterstand_gemiddeld = ts_pb_gegevens[BUITENWATERSTAND_KOLOM].mean()
        else:
            buitenwaterstand_gemiddeld = algemene_config["buitenwaterstand"]
    else:
        buitenwaterstand_gemiddeld = buitenwaterstand_data

    # --- Analyse (met gemiddelde waterstand) ---
    maatgevend_boezempeil = (
        buitenwaterstand_gemiddeld + algemene_config["maatgevend_boezempeil_offset"]
    )
    teensloot_peil = float(profiel["teensloot_peil"])

    gem_waterstanden, freatische_lijn, plot_schematisatie, x_ts_min, x_ts_max, heeft_kruin_peilbuis = (
        analyse.bereken_waterstand_lijnen(
            dijkprofiel,
            buitenwaterstand_gemiddeld,
            maatgevend_boezempeil,
            teensloot_peil,
            x_peilbuis,
            y_peilbuis_mv,
            gemiddelden,
            metadata_pb["Code_peilbuis"].tolist(),
        )
    )

    # Data voor profiel plot (hoofdprofiel + buitenwaterstand tijdserie)
    kolommen_voor_profielplot = stijghoogte_namen.copy()
    if BUITENWATERSTAND_KOLOM in ts_pb_gegevens.columns:
        if BUITENWATERSTAND_KOLOM not in kolommen_voor_profielplot:
            kolommen_voor_profielplot.append(BUITENWATERSTAND_KOLOM)

    plot_data: dict = {
        "profiel": profiel,
        "pb_gegevens": ts_pb_gegevens[kolommen_voor_profielplot],
        "geometry_by_soil": geometrie_per_grondsoort,
        "metadata_pb": metadata_pb,
        "x_peilbuis": x_peilbuis,
        "stijghoogtes": stijghoogtes,
        "gem_waterstanden": gem_waterstanden,
        "freatische_lijn": freatische_lijn,
        "plot_schematisatie": plot_schematisatie,
        "x_ts_min": x_ts_min,
        "x_ts_max": x_ts_max,
        "teensloot_peil": teensloot_peil,
        "heeft_kruin_peilbuis": heeft_kruin_peilbuis,
        "buitenwaterstand": buitenwaterstand_gemiddeld,
        "dijkprofiel": dijkprofiel,
        # Staan voor de MVP uit (plotten.ONDERDELEN_STANDAARD); er is geen bestand meer.
        "return_periods_data": None,
        "project_folder_name": ctx.project,
    }
    for sleutel, diep in (("A", diep_a), ("B", diep_b)):
        plot_data |= {
            f"profiel_{sleutel}": diep.profiel if diep else None,
            f"pb_gegevens_{sleutel}": diep.pb_gegevens if diep else None,
            f"metadata_pb_{sleutel}": diep.metadata_zonder_labels() if diep else None,
            f"x_peilbuis_{sleutel}": diep.x_peilbuis if diep else None,
            f"stijghoogtes_{sleutel}": diep.stijghoogtes if diep else None,
        }

    profiel_figuur = plotten.maak_profiel_plot(plot_data, algemene_config)

    # De metadata van de tijdreeksplot moet overeenkomen met de kolommen in
    # ts_pb_gegevens (exclusief buitenwaterstand).
    ts_metadata_pb = pd.concat(
        [metadata_pb, *(d.metadata_pb for d in (diep_a, diep_b) if d is not None)],
        axis=0,
    )
    # De neerslag loopt over alle projecten samen; alleen de meetperiode telt.
    neerslag: DataFrame = ctx.neerslag_data.loc[ts_pb_gegevens.index.min():ts_pb_gegevens.index.max()]
    tijdreeks_figuur = plotten.maak_tijdreeks_plot(
        ts_pb_gegevens, profiel_naam, ts_metadata_pb, neerslag
    )

    return ProfielFiguren(
        profiel_naam, uitvoer_stam, profiel_figuur, tijdreeks_figuur, ontbrekend + ontbrekend_diep
    )
