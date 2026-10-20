# kaart_generator.py
import folium
import numpy as np
import pandas as pd
from pandas import DataFrame
import pyproj
from pathlib import Path


MARKER_KLEUREN: list[str] = [
    "blue", "red", "green", "purple", "orange", "darkred", "cadetblue", "darkgreen",
]

# Esri's gratis "World Topo Map"-tiles: geen API key nodig (in tegenstelling tot de
# OpenStreetMap- en CartoDB-tegelservers, die inmiddels 403's resp. "API KEY REQUIRED"
# geven voor dit soort lokaal/offline gebruik).
ACHTERGROND_TILES_URL = (
    "https://server.arcgisonline.com/ArcGIS/rest/services/"
    "World_Topo_Map/MapServer/tile/{z}/{y}/{x}"
)
ACHTERGROND_TILES_ATTRIBUTIE = "Tiles &copy; Esri &mdash; Source: Esri, DeLorme, NAVTEQ"


def verzamel_profiel_locaties(
    profielen_df: DataFrame,
    stix_bestanden: list[Path],
    project_map_naam: str,
    link_prefix: str = "",
) -> list[dict]:
    """Bepaalt per profiel de kaartlocatie (lat/lon) en de bestandsnaam van de bijbehorende plot."""
    beschikbare_stix: set[str] = {f.name for f in stix_bestanden}

    transformator = pyproj.Transformer.from_crs(
        "EPSG:28992", "EPSG:4326", always_xy=True
    )

    profiel_locaties = []
    for idx, profiel_rij in profielen_df.iterrows():
        profiel_naam = profiel_rij["naam"]

        # Sub-profielen (A/B) krijgen geen eigen plotbestand: die worden in main.py
        # verwerkt onder het hoofdprofiel. Sla ze hier ook over.
        if str(profiel_rij.get("diepte_niveau", "hoofd")) != "hoofd":
            continue

        # Bestandsnaamlogica moet gelijk zijn aan die in main.py: gebruik de stem van
        # het stix-bestand uit het werkboek, anders de profielnaam met underscores.
        stix_bestand: str = str(profiel_rij.get("stix_bestand") or "")
        uitvoer_stam = profiel_naam.replace(" ", "_")
        if stix_bestand and stix_bestand in beschikbare_stix:
            uitvoer_stam = Path(stix_bestand).stem

        if pd.notna(profiel_rij.get("x")) and pd.notna(profiel_rij.get("y")):
            x_rd, y_rd = profiel_rij["x"], profiel_rij["y"]
            lon, lat = transformator.transform(x_rd, y_rd)

            plot_html_naam = f"{link_prefix}plot_{project_map_naam}_{uitvoer_stam}.html"
            profiel_locaties.append(
                {
                    "project": project_map_naam,
                    "naam": profiel_naam,
                    "lat": lat,
                    "lon": lon,
                    "html": plot_html_naam,
                }
            )

    return profiel_locaties


def maak_folium_kaart(
    profielen_df: DataFrame,
    stix_bestanden: list[Path],
    opslag_map: Path,
    project_map_naam: str,
) -> None:
    """Genereert een Folium-kaart met markers voor elk profiel die naar hun plot linken."""

    profiel_locaties = verzamel_profiel_locaties(
        profielen_df, stix_bestanden, project_map_naam
    )

    if profiel_locaties:
        gem_lat = np.mean([loc["lat"] for loc in profiel_locaties])
        gem_lon = np.mean([loc["lon"] for loc in profiel_locaties])
        m = folium.Map(
            location=[gem_lat, gem_lon],
            zoom_start=12,
            tiles=ACHTERGROND_TILES_URL,
            attr=ACHTERGROND_TILES_ATTRIBUTIE,
        )

        for locatie in profiel_locaties:
            plot_pad = Path(f"{opslag_map}/plots/") / locatie["html"]
            if plot_pad.exists():
                popup_html = f"<b>{locatie['naam']}</b><br><a href='{locatie['html']}' target='_blank'>Bekijk plot</a>"
            else:
                popup_html = f"<b>{locatie['naam']}</b><br>Plot niet beschikbaar"

            folium.Marker(
                location=[locatie["lat"], locatie["lon"]],
                popup=folium.Popup(popup_html, max_width=300),
                tooltip=locatie["naam"],
                icon=folium.Icon(color="blue", icon="info-sign"),
            ).add_to(m)

        html_map_pad = Path(f"{opslag_map}/plots/Overzichtskaart_Profielen.html")
        m.save(str(html_map_pad))
        print(f"✅ HTML kaart opgeslagen als: {html_map_pad}")
    else:
        print("⚠️ Geen profiel locaties met coördinaten gevonden voor folium kaart.")


def maak_gecombineerde_folium_kaart(
    locaties_per_project: dict[str, list[dict]],
    opslag_pad: Path,
    bestandsnaam: str = "Overzichtskaart_Alle_Projecten.html",
) -> None:
    """
    Genereert één Folium-kaart met de profielen van meerdere projecten samen.

    Elk project krijgt een eigen aan-/uitzetbare laag (met eigen markerkleur) via
    folium.LayerControl, zodat je projecten kunt combineren of los kunt bekijken.
    De 'html'-waarde in elke locatie moet al het relatieve pad t.o.v. `opslag_pad`
    naar het bijbehorende plotbestand bevatten (bijv. 'Beemster/plots/plot_..._DWP001.html').
    """
    alle_locaties = [loc for locs in locaties_per_project.values() for loc in locs]

    if not alle_locaties:
        print("⚠️ Geen profiel locaties gevonden voor de gecombineerde folium kaart.")
        return

    gem_lat = np.mean([loc["lat"] for loc in alle_locaties])
    gem_lon = np.mean([loc["lon"] for loc in alle_locaties])
    m = folium.Map(
        location=[gem_lat, gem_lon],
        zoom_start=11,
        tiles=ACHTERGROND_TILES_URL,
        attr=ACHTERGROND_TILES_ATTRIBUTIE,
    )

    for i, (project_naam, locaties) in enumerate(locaties_per_project.items()):
        if not locaties:
            continue

        kleur = MARKER_KLEUREN[i % len(MARKER_KLEUREN)]
        laag = folium.FeatureGroup(name=project_naam, show=True)

        for locatie in locaties:
            plot_pad = Path(opslag_pad) / locatie["html"]
            if plot_pad.exists():
                popup_html = (
                    f"<b>{project_naam} - {locatie['naam']}</b><br>"
                    f"<a href='{locatie['html']}' target='_blank'>Bekijk plot</a>"
                )
            else:
                popup_html = f"<b>{project_naam} - {locatie['naam']}</b><br>Plot niet beschikbaar"

            folium.Marker(
                location=[locatie["lat"], locatie["lon"]],
                popup=folium.Popup(popup_html, max_width=300),
                tooltip=f"{project_naam} - {locatie['naam']}",
                icon=folium.Icon(color=kleur, icon="info-sign"),
            ).add_to(laag)

        laag.add_to(m)

    folium.LayerControl(collapsed=False).add_to(m)

    html_map_pad = Path(opslag_pad) / bestandsnaam
    m.save(str(html_map_pad))
    print(f"✅ Gecombineerde HTML kaart opgeslagen als: {html_map_pad}")
