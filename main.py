# main.py
import argparse
import sys
from pathlib import Path
import traceback
import numpy as np
import pandas as pd
import plotly.io as pio
from pandas import DataFrame, Series

# Forceer UTF-8 voor stdout/stderr zodat emoji's (✅⚠️❌) in print-statements
# geen UnicodeEncodeError geven op Windows-consoles met een niet-UTF-8 codepage (bv. cp1252).
sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

# Importeren van de WV2030 module
from Modules import (
    analyse,
    data_lader,
    kaart_generator,
    paths,
    plotten,
    stix_verwerker,
    workbook_lader,
)

pio.renderers.default = "browser"


def parseer_x_coordinaten(waarde: any) -> list[float]:
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


def main(project_map_naam: str, strict: bool = False) -> int:
    """
    Hoofdfunctie om het proces voor het genereren van plots te coördineren.

    Geeft het aantal mislukte profielen terug, zodat een aanroeper (of een job)
    kan zien of er echt iets gemaakt is.
    """
    # === Configuratie Laden ===
    # Alles komt uit het werkboek input_data/<project>/<project>.xlsx; de paden
    # eromheen staan in Modules/paths.py.
    invoer = workbook_lader.laad_project(project_map_naam)
    YAML_CONFIG: dict = invoer.algemene_config
    PLOTS_PAD: Path = paths.plots_map(project_map_naam)

    bevindingen = workbook_lader.valideer(invoer)
    for bevinding in bevindingen:
        print(f"{'❌' if bevinding.ernst == 'fout' else '⚠️'} {bevinding}")
    fouten = [b for b in bevindingen if b.ernst == "fout"]
    if fouten:
        raise ValueError(
            f"{len(fouten)} fout(en) in {invoer.workbook.name}; los die eerst op."
        )

    RUWE_DATA_PAD: Path | None = invoer.paden["peilbuisdata"]
    if RUWE_DATA_PAD is None:
        raise FileNotFoundError(
            f"Geen peilbuisdata-bestand gevonden voor {project_map_naam}. "
            "Controleer de sleutel 'peilbuisdata_bestand' in het blad 'project'."
        )

    neerslag_data = data_lader.laad_neerslag_data(invoer.paden["neerslag"])

    terugkeertijden_data = None
    if invoer.paden["terugkeertijden"]:
        terugkeertijden_data = data_lader.laad_terugkeertijden_data(
            invoer.paden["terugkeertijden"]
        )

    algemene_config: dict = invoer.algemene_config
    profielen_df: DataFrame = invoer.profielen_df

    # --- Buitenwaterstand laden (fluctuerend of vast) ---
    buitenwaterstand_bestand = invoer.paden["buitenwaterstand"]
    buitenwaterstand_data = None

    if buitenwaterstand_bestand:
        bestand_pad = buitenwaterstand_bestand
        try:
            print(f"Fluctuerende buitenwaterstand wordt ingelezen uit: {bestand_pad}")
            df_bw = pd.read_excel(bestand_pad, engine="openpyxl")

            # Zoek naar datum- en waterstandkolommen (flexibel)
            datum_kolom = next((col for col in df_bw.columns if 'datum' in col.lower() or 'date' in col.lower()), None)
            waterstand_kolom = next((col for col in df_bw.columns if 'waterstand' in col.lower() or 'niveau' in col.lower()), None)

            if datum_kolom and waterstand_kolom:
                df_bw = df_bw.rename(columns={datum_kolom: 'datum', waterstand_kolom: 'waterstand'})
                df_bw['datum'] = pd.to_datetime(df_bw['datum'])
                df_bw = df_bw.set_index('datum')
                buitenwaterstand_data = df_bw[['waterstand']]
                print("✅ Fluctuerende buitenwaterstand succesvol geladen.")
            else:
                print(f"⚠️ WAARSCHUWING: Kon 'datum' of 'waterstand' kolom niet vinden in {bestand_pad}. Fallback naar vaste waterstand.")
                buitenwaterstand_data = algemene_config["buitenwaterstand"]
        except FileNotFoundError:
            print(f"⚠️ WAARSCHUWING: Bestand niet gevonden: {bestand_pad}. Fallback naar vaste waterstand.")
            buitenwaterstand_data = algemene_config["buitenwaterstand"]
        except Exception as e:
            print(f"❌ Fout bij lezen van buitenwaterstand-bestand: {e}. Fallback naar vaste waterstand.")
            buitenwaterstand_data = algemene_config["buitenwaterstand"]
    else:
        print("Vaste buitenwaterstand wordt gebruikt uit general_config.")
        buitenwaterstand_data = algemene_config["buitenwaterstand"]

    # === STIX-bestanden ===
    # Het werkboek benoemt per profiel het stix-bestand in de kolom
    # 'stix_bestand'; er wordt niet meer op een DWP-nummer in de bestandsnaam
    # gezocht, en de profielnaam hoeft dus niet exact 'Profiel <n>' te zijn.
    stix_bestanden: list[Path] = sorted(paths.stix_map(project_map_naam).glob("*.stix"))
    if not stix_bestanden:
        print(f"⚠️ Geen stix-bestanden gevonden in map: {paths.stix_map(project_map_naam)}")

    gelukt: int = 0
    mislukt: list[str] = []
    overgeslagen: int = 0

    # === Verwerk elk profiel uit de configuratie ===
    for profiel_index, profiel in profielen_df.iterrows():
        profiel_naam: str = profiel["naam"]
        profiel_id: str = profiel["profiel_id"]
        if profiel["diepte_niveau"] != "hoofd":
            print(
                f"--- Overslaan sub-profiel: {profiel_naam} (wordt verwerkt onder hoofdprofiel) ---"
            )
            continue
        if not profiel.get("actief", True):
            print(f"--- Overslaan profiel: {profiel_naam} (actief = FALSE) ---")
            overgeslagen += 1
            continue
        print(f"--- Verwerk profiel: {profiel_naam} ---")

        # --- Data Laden (dwarsprofielgeometrie uit het werkboek) ---
        coords_df = workbook_lader.laad_coords(invoer, profiel_id)
        dijkprofiel = coords_df[["x", "y"]].to_numpy()

        # --- Zoek STIX-bestand en bepaal eventuele horizontale correctie ---
        # De STIX-geometrie (grondopbouw) is leidend qua ligging en wordt zelf niet
        # verplaatst. Het Excel-dijkprofiel (en alles wat daaruit wordt afgeleid, zoals
        # peilbuisposities en de freatische lijn) wordt zo nodig horizontaal verschoven
        # zodat het aansluit op de STIX-geometrie.
        stix_bestand = workbook_lader.stix_pad(invoer, profiel_id)
        ruwe_geometrie_per_grondsoort = {}
        offset_x = 0.0

        if stix_bestand:
            print(f"✅ Stix-bestand gevonden: {stix_bestand.name}")
            ruwe_geometrie_per_grondsoort = stix_verwerker.verwerk_stix_bestand(
                stix_bestand
            )

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
                    print(
                        "   ⚠️ Resterende afwijking na correctie is nog aanzienlijk "
                        f"(RMSE {rmse_met:.2f}m). Controleer dit profiel handmatig; evt. te "
                        "corrigeren via kolom 'stix_offset_x_handmatig' in de profielen-Excel."
                    )

            dijkprofiel[:, 0] += offset_x
        else:
            print(
                f"⚠️ Geen stix-bestand gevonden voor profiel '{profiel_naam}'. Plot wordt zonder grondopbouw gemaakt."
            )

        # --- Zoek naar diepe peilbuizen ('A' profiel) ---
        profiel_A = None
        metadata_pb_A = None
        x_peilbuis_A = None
        pb_gegevens_A = None
        stijghoogtes_A = None
        stijghoogte_namen_A = None

        profiel_naam_A = f"{profiel['naam']}A"
        profiel_rij_A = profielen_df[profielen_df["naam"] == profiel_naam_A]

        if not profiel_rij_A.empty:
            print(f"✅ Diep profiel gevonden: {profiel_naam_A}")
            profiel_A = profiel_rij_A.iloc[0]
            try:
                metadata_pb_A = workbook_lader.metadata_voor_profiel(
                    invoer, profiel_A["profiel_id"]
                )

                x_peilbuis_A = [
                    x + offset_x
                    for x in parseer_x_coordinaten(
                        profiel_A["x_coordinaten_dwarsprofiel"]
                    )
                ]

                stijghoogte_namen_A = str(profiel_A["stijghoogte_namen"]).split(",")
                pb_gegevens_A = data_lader.laad_peilbuis_data(
                    RUWE_DATA_PAD, stijghoogte_namen_A
                )
                stijghoogtes_A = [
                    pb_gegevens_A[naam].dropna().to_numpy()
                    for naam in stijghoogte_namen_A
                ]
            except Exception as e:
                print(f"❌ Fout bij laden van diep profiel data: {e}")
                profiel_A = None  # Reset bij fout

        # --- Zoek naar nog diepere peilbuizen ('B' profiel) ---
        profiel_B = None
        metadata_pb_B = None
        x_peilbuis_B = None
        pb_gegevens_B = None
        stijghoogtes_B = None
        stijghoogte_namen_B = None

        profiel_naam_B = f"{profiel['naam']}B"
        profiel_rij_B = profielen_df[profielen_df["naam"] == profiel_naam_B]

        if not profiel_rij_B.empty:
            print(f"✅ Nog dieper profiel gevonden: {profiel_naam_B}")
            profiel_B = profiel_rij_B.iloc[0]
            try:
                metadata_pb_B = workbook_lader.metadata_voor_profiel(
                    invoer, profiel_B["profiel_id"]
                )

                x_peilbuis_B = [
                    x + offset_x
                    for x in parseer_x_coordinaten(
                        profiel_B["x_coordinaten_dwarsprofiel"]
                    )
                ]

                stijghoogte_namen_B = str(profiel_B["stijghoogte_namen"]).split(",")
                pb_gegevens_B = data_lader.laad_peilbuis_data(
                    RUWE_DATA_PAD, stijghoogte_namen_B
                )
                stijghoogtes_B = [
                    pb_gegevens_B[naam].dropna().to_numpy()
                    for naam in stijghoogte_namen_B
                ]
            except Exception as e:
                print(f"❌ Fout bij laden van nog dieper profiel data: {e}")
                profiel_B = None  # Reset bij fout

        try:
            # STIX-bestand, geometrie en x-correctie zijn hierboven al bepaald.
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
            y_peilbuis_mv = [
                analyse.vind_y_in_dwarsprofiel(dijkprofiel, x) for x in x_peilbuis
            ]

            stijghoogte_namen = str(profiel["stijghoogte_namen"]).split(",")
            pb_gegevens: DataFrame = data_lader.laad_peilbuis_data(
                RUWE_DATA_PAD, stijghoogte_namen
            )
            stijghoogtes = [
                pb_gegevens[naam].dropna().to_numpy() for naam in stijghoogte_namen
            ]
            gemiddelden = [np.mean(data) for data in stijghoogtes if len(data) > 0]

            # --- Combineer alle peilbuisdata en integreer buitenwaterstand ---
            ts_pb_gegevens = pb_gegevens.copy()
            if pb_gegevens_A is not None:
                ts_pb_gegevens = pd.concat([ts_pb_gegevens, pb_gegevens_A], axis=1)
            if pb_gegevens_B is not None:
                ts_pb_gegevens = pd.concat([ts_pb_gegevens, pb_gegevens_B], axis=1)

            buitenwaterstand_gemiddeld = None
            if isinstance(buitenwaterstand_data, pd.DataFrame):
                ts_pb_gegevens = ts_pb_gegevens.sort_index()
                bw_df_sorted = buitenwaterstand_data.sort_index()
                
                # Voeg de waterstand toe aan de gecombineerde dataset
                ts_pb_gegevens = pd.merge_asof(
                    ts_pb_gegevens,
                    bw_df_sorted.rename(columns={'waterstand': 'Buitenwaterstand Markermeer'}),
                    left_index=True,
                    right_index=True,
                    direction='nearest'
                )
                if 'Buitenwaterstand Markermeer' in ts_pb_gegevens.columns and not ts_pb_gegevens['Buitenwaterstand Markermeer'].empty:
                    buitenwaterstand_gemiddeld = ts_pb_gegevens['Buitenwaterstand Markermeer'].mean()
                else:
                    buitenwaterstand_gemiddeld = algemene_config["buitenwaterstand"]
            else:
                buitenwaterstand_gemiddeld = buitenwaterstand_data

            # --- Analyse (met gemiddelde waterstand) ---
            maatgevend_boezempeil = (
                buitenwaterstand_gemiddeld + algemene_config["maatgevend_boezempeil_offset"]
            )
            teensloot_peil = float(profiel["teensloot_peil"])

            # --- Analyse ---
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
            if 'Buitenwaterstand Markermeer' in ts_pb_gegevens.columns:
                # Voeg kolom toe, maar alleen als deze niet al bestaat
                if 'Buitenwaterstand Markermeer' not in kolommen_voor_profielplot:
                    kolommen_voor_profielplot.append('Buitenwaterstand Markermeer')

            # Maak kopieën van metadata voor A en B profielen en wis de labels.
            # Dit voorkomt dat de labels van de diepere peilbuizen (profiel A/B)
            # de labels van het hoofdprofiel overschrijven of ermee conflicteren in de plot.
            # We willen enkel de labels van het hoofdprofiel tonen.

            metadata_pb_A_plot = None
            if metadata_pb_A is not None:
                metadata_pb_A_plot = metadata_pb_A.copy()
                metadata_pb_A_plot["Code_peilbuis"] = [""] * len(metadata_pb_A_plot)

            metadata_pb_B_plot = None
            if metadata_pb_B is not None:
                metadata_pb_B_plot = metadata_pb_B.copy()
                metadata_pb_B_plot["Code_peilbuis"] = [""] * len(metadata_pb_B_plot)

            # --- Plotten ---
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
                "return_periods_data": terugkeertijden_data,
                "project_folder_name": project_map_naam,
                # Data voor diepe peilbuizen
                "profiel_A": profiel_A,
                "pb_gegevens_A": pb_gegevens_A,
                "metadata_pb_A": metadata_pb_A_plot,
                "x_peilbuis_A": x_peilbuis_A,
                "stijghoogtes_A": stijghoogtes_A,
                # Data voor nog diepere peilbuizen
                "profiel_B": profiel_B,
                "pb_gegevens_B": pb_gegevens_B,
                "metadata_pb_B": metadata_pb_B_plot,
                "x_peilbuis_B": x_peilbuis_B,
                "stijghoogtes_B": stijghoogtes_B,
            }

            figuur = plotten.maak_profiel_plot(plot_data, YAML_CONFIG)

            # --- Tijdreeks Plot Data Voorbereiding ---
            # Bouw de gecombineerde metadata voor de tijdreeksplot.
            # Dit moet overeenkomen met de kolommen in ts_pb_gegevens (exclusief buitenwaterstand).
            ts_metadata_pb = metadata_pb.copy()
            if metadata_pb_A is not None:
                ts_metadata_pb = pd.concat([ts_metadata_pb, metadata_pb_A], axis=0)
            if metadata_pb_B is not None:
                ts_metadata_pb = pd.concat([ts_metadata_pb, metadata_pb_B], axis=0)

            figuur_tijdreeksen = plotten.maak_tijdreeks_plot(
                ts_pb_gegevens,
                plot_data["profiel"]["naam"],
                ts_metadata_pb,
                neerslag_data,
            )

            uitvoer_bestandsnaam = (
                PLOTS_PAD / f"plot_{project_map_naam}_{uitvoer_stam}.html"
            )
            with open(uitvoer_bestandsnaam, "w", encoding="utf-8") as f:
                f.write(
                    figuur.to_html(
                        full_html=False,
                        include_plotlyjs="cdn",
                        div_id=plotten.PROFIEL_PLOT_DIV_ID,
                    )
                )
                f.write(
                    figuur_tijdreeksen.to_html(
                        full_html=False,
                        include_plotlyjs=False,
                        div_id=plotten.TIJDREEKS_PLOT_DIV_ID,
                    )
                )
                f.write(plotten.SLIDER_SYNC_SCRIPT)
            print(f"✅ Plot opgeslagen als: {uitvoer_bestandsnaam}\n")
            gelukt += 1

        except Exception as e:
            print(f"❌ Fout bij volledige verwerking van {profiel['naam']}: {e}")
            traceback.print_exc()
            mislukt.append(str(profiel["naam"]))
            if strict:
                raise
            continue

    # --- Genereer Folium Kaart ---
    kaart_generator.maak_folium_kaart(
        profielen_df,
        stix_bestanden,
        paths.repo_root() / paths.UITVOER_MAP / project_map_naam,
        project_map_naam,
    )

    print(
        f"\n=== {project_map_naam}: {gelukt} plot(s) gemaakt, "
        f"{len(mislukt)} mislukt, {overgeslagen} overgeslagen ==="
    )
    if mislukt:
        print(f"    mislukt: {', '.join(mislukt)}")
    return len(mislukt)


def _argumenten() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Genereert de boxplot-/dwarsprofielplots voor een project."
    )
    groep = parser.add_mutually_exclusive_group(required=True)
    groep.add_argument(
        "--project",
        action="append",
        choices=paths.projecten(),
        metavar="NAAM",
        help=f"projectnaam; mag meerdere keren. Keuze uit: {', '.join(paths.projecten())}",
    )
    groep.add_argument("--alle", action="store_true", help="alle projecten")
    parser.add_argument(
        "--strict",
        action="store_true",
        help="stop bij de eerste fout in plaats van doorgaan naar het volgende profiel",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = _argumenten()
    te_doen: list[str] = paths.projecten() if args.alle else args.project

    mislukt_totaal: int = 0
    niets_gemaakt: list[str] = []
    for naam in te_doen:
        try:
            mislukt_totaal += main(naam, strict=args.strict)
        except Exception as fout:
            print(f"❌ {naam} volledig mislukt: {fout}")
            traceback.print_exc()
            if args.strict:
                raise
            niets_gemaakt.append(naam)

    # Een job die nul plots oplevert mag niet als succes eindigen.
    if mislukt_totaal or niets_gemaakt:
        if niets_gemaakt:
            print(f"\n❌ Projecten zonder uitvoer: {', '.join(niets_gemaakt)}")
        sys.exit(1)
