# main.py
import argparse
import sys
from pathlib import Path
import traceback
import plotly.io as pio

# Forceer UTF-8 voor stdout/stderr zodat emoji's (✅⚠️❌) in print-statements
# geen UnicodeEncodeError geven op Windows-consoles met een niet-UTF-8 codepage (bv. cp1252).
sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

# Importeren van de WV2030 module
from Modules import kaart_generator, paths, profiel as profiel_module, workbook_lader

pio.renderers.default = "browser"


def main(project_map_naam: str, strict: bool = False) -> int:
    """
    Hoofdfunctie om het proces voor het genereren van plots te coördineren.

    Geeft het aantal mislukte profielen terug, zodat een aanroeper (of een job)
    kan zien of er echt iets gemaakt is.

    De verwerking per profiel zit in `Modules/profiel.py`, die ook door de
    Streamlit-app (`app.py`) gebruikt wordt; hier worden de figuren alleen als
    HTML weggeschreven.
    """
    # === Configuratie Laden ===
    # Alles komt uit de centrale invoer in input_data/ (config.csv, projecten.csv,
    # D-Stability/, dwarsprofielen/; zie workbook_lader); de paden staan in Modules/paths.py.
    ctx = profiel_module.laad_project_context(project_map_naam)
    laad_reeksen = profiel_module.lokale_reeksen(ctx.invoer)
    PLOTS_PAD: Path = paths.plots_map(project_map_naam)
    profielen_df = ctx.invoer.profielen_df

    # === STIX-bestanden ===
    # Eén map voor alle projecten; het stix-bestand van een dwarsprofiel heet
    # naar zijn sleutel: D-Stability/BE_0072.stix.
    stix_bestanden: list[Path] = sorted(paths.stix_map().glob("*.stix"))
    if not stix_bestanden:
        print(f"⚠️ Geen stix-bestanden gevonden in map: {paths.stix_map()}")

    gelukt: int = 0
    mislukt: list[str] = []
    overgeslagen: int = 0

    # === Verwerk elk profiel uit de configuratie ===
    for _, profiel in profielen_df.iterrows():
        profiel_naam: str = profiel["naam"]
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

        try:
            figuren = profiel_module.maak_profiel_figuren(
                ctx, profiel["profiel_id"], laad_reeksen
            )
            uitvoer_bestandsnaam = (
                PLOTS_PAD / f"plot_{project_map_naam}_{figuren.uitvoer_stam}.html"
            )
            with open(uitvoer_bestandsnaam, "w", encoding="utf-8") as f:
                f.write(figuren.als_html())
            print(f"✅ Plot opgeslagen als: {uitvoer_bestandsnaam}\n")
            gelukt += 1

        except Exception as e:
            print(f"❌ Fout bij volledige verwerking van {profiel_naam}: {e}")
            traceback.print_exc()
            mislukt.append(str(profiel_naam))
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
    for los in workbook_lader.losse_bestanden():
        print(f"⚠️ {los.relative_to(paths.repo_root())} hoort bij geen dwarsprofiel in config.csv")

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
