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
from pathlib import Path

from Modules import config_lader, kaart_generator

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

PROJECTEN_PAD = Path(__file__).parent / "Projecten"


def verzamel_locaties_voor_project(project_pad: Path) -> tuple[str, list[dict]]:
    """Laadt de configuratie van één project en bepaalt de kaartlocaties van de profielen."""
    project_naam = project_pad.name
    yaml_config: dict = config_lader.laad_yaml_config(
        project_pad / "config" / "config.yaml"
    )
    basis_pad: Path = project_pad / yaml_config["base_data_dir"]

    _, profielen_df = config_lader.laad_excel_config(
        basis_pad, yaml_config["config_file"], yaml_config["config_sheets"]
    )

    stix_map = basis_pad / yaml_config.get("stix_folder", "stix_files")
    stix_bestanden = sorted(stix_map.glob("*.stix")) if stix_map.exists() else []

    locaties = kaart_generator.verzamel_profiel_locaties(
        profielen_df,
        stix_bestanden,
        project_naam,
        link_prefix=f"{project_naam}/plots/",
    )
    return project_naam, locaties


def main() -> None:
    project_mappen = sorted(
        p for p in PROJECTEN_PAD.iterdir()
        if p.is_dir() and (p / "config" / "config.yaml").exists()
    )

    locaties_per_project: dict[str, list[dict]] = {}
    for project_pad in project_mappen:
        try:
            project_naam, locaties = verzamel_locaties_voor_project(project_pad)
            print(f"✅ {project_naam}: {len(locaties)} profiel(en) met coördinaten gevonden.")
            locaties_per_project[project_naam] = locaties
        except Exception as e:
            print(f"❌ Fout bij verwerken van project '{project_pad.name}': {e}")

    kaart_generator.maak_gecombineerde_folium_kaart(locaties_per_project, PROJECTEN_PAD)


if __name__ == "__main__":
    main()
