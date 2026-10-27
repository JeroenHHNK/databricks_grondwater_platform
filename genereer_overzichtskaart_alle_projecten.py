# genereer_overzichtskaart_alle_projecten.py
"""
Bouwt één gecombineerde Folium-kaart met de profielen van alle projecten in de map
'Projecten'. Elk project krijgt een eigen aan-/uitzetbare laag, en elke marker
linkt naar de al gegenereerde profielplot van dat project (dus eerst main.py per
project draaien om de plots te maken/actualiseren).

De plots blijven staan waar ze al staan (Projecten/<project>/plots/...): er wordt
niets gekopieerd. De gecombineerde kaart komt in Projecten/Overzichtskaart_Alle_Projecten.html
en linkt met relatieve paden naar <project>/plots/<bestand>.html.
"""
import sys

from Modules import kaart_generator, paths, workbook_lader

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")


def verzamel_locaties_voor_project(project_naam: str) -> list[dict]:
    """Laadt het werkboek van één project en bepaalt de kaartlocaties van de profielen."""
    invoer = workbook_lader.laad_project(project_naam)
    stix_bestanden = sorted(paths.stix_map().glob("*.stix"))

    return kaart_generator.verzamel_profiel_locaties(
        invoer.profielen_df,
        stix_bestanden,
        project_naam,
        link_prefix=f"{project_naam}/plots/",
    )


def main() -> None:
    locaties_per_project: dict[str, list[dict]] = {}
    for project_naam in paths.projecten():
        try:
            locaties = verzamel_locaties_voor_project(project_naam)
            print(f"✅ {project_naam}: {len(locaties)} profiel(en) met coördinaten gevonden.")
            locaties_per_project[project_naam] = locaties
        except Exception as e:
            print(f"❌ Fout bij verwerken van project '{project_naam}': {e}")

    kaart_generator.maak_gecombineerde_folium_kaart(
        locaties_per_project, paths.repo_root() / paths.UITVOER_MAP
    )


if __name__ == "__main__":
    main()
