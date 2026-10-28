# kaart_generator.py
"""
De overzichtskaart: alle dwarsprofielen van alle projecten, een kleur per project.

Een Plotly-kaart (MapLibre) in plaats van folium, zodat de Streamlit-app een
klik op een marker terugkrijgt (`st.plotly_chart(on_select=...)`). Elk punt
draagt `[project, profiel_id]` als `customdata`.
"""

import pandas as pd
import plotly.graph_objects as go
import pyproj
from pandas import DataFrame

# Esri's gratis "World Topo Map"-tiles: geen API key nodig (in tegenstelling tot de
# OpenStreetMap- en CartoDB-tegelservers, die 403's resp. "API KEY REQUIRED"
# kunnen geven).
ACHTERGROND_TILES_URL = (
    "https://server.arcgisonline.com/ArcGIS/rest/services/"
    "World_Topo_Map/MapServer/tile/{z}/{y}/{x}"
)
ACHTERGROND_TILES_ATTRIBUTIE = "Tiles &copy; Esri &mdash; Source: Esri, DeLorme, NAVTEQ"

# Vaste volgorde, op alfabet van de projectnaam; een project houdt zijn kleur
# zolang er geen project vóór in het alfabet bijkomt. Gecontroleerd op
# kleurenblindheid (dataviz validate_palette, licht).
PROJECT_KLEUREN: list[str] = [
    "#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948",
]
GEEN_DATA_KLEUR = "#8a8a85"

_RD_NAAR_WGS84 = pyproj.Transformer.from_crs("EPSG:28992", "EPSG:4326", always_xy=True)


def profiel_locaties(profielen_df: DataFrame, project: str) -> DataFrame:
    """Per hoofdprofiel met coördinaten: project, profiel_id, naam, lat, lon."""
    hoofd: DataFrame = profielen_df[
        (profielen_df["diepte_niveau"] == "hoofd") & profielen_df["x"].notna() & profielen_df["y"].notna()
    ]
    lon, lat = _RD_NAAR_WGS84.transform(hoofd["x"].to_numpy(float), hoofd["y"].to_numpy(float))
    return DataFrame({
        "project": project,
        "profiel_id": hoofd["profiel_id"].to_numpy(),
        "naam": hoofd["naam"].to_numpy(),
        "lat": lat,
        "lon": lon,
    })


def maak_overzichtskaart(locaties: DataFrame, hoogte: int = 650) -> go.Figure:
    """
    Eén trace per project, plus een trace voor profielen zonder stijghoogtes
    als `locaties` een kolom `heeft_data` heeft.

    Elke marker krijgt een witte ring (een grotere witte marker eronder), zodat
    de lichtere kleuren ook op de topografische achtergrond zichtbaar blijven.
    """
    projecten: list[str] = sorted(locaties["project"].unique())
    kleur: dict[str, str] = {p: PROJECT_KLEUREN[i % len(PROJECT_KLEUREN)] for i, p in enumerate(projecten)}
    heeft_data: pd.Series = locaties["heeft_data"] if "heeft_data" in locaties else pd.Series(True, locaties.index)

    figuur = go.Figure()
    figuur.add_trace(go.Scattermap(
        lat=locaties["lat"], lon=locaties["lon"], mode="markers",
        marker={"size": 15, "color": "white"}, hoverinfo="skip", showlegend=False,
        name="ring",
    ))
    groepen: list[tuple[str, pd.Series, str]] = [
        (p, (locaties["project"] == p) & heeft_data, kleur[p]) for p in projecten
    ]
    if not heeft_data.all():
        groepen.append(("geen stijghoogtes", ~heeft_data, GEEN_DATA_KLEUR))
    for naam, masker, k in groepen:
        deel: DataFrame = locaties[masker]
        if deel.empty:
            continue
        figuur.add_trace(go.Scattermap(
            lat=deel["lat"], lon=deel["lon"], mode="markers", name=naam,
            marker={"size": 11, "color": k},
            customdata=deel[["project", "profiel_id"]].to_numpy(),
            text=deel["naam"],
            hovertemplate="<b>%{text}</b><br>%{customdata[0]}<extra></extra>",
        ))

    figuur.update_layout(
        height=hoogte,
        margin={"l": 0, "r": 0, "t": 0, "b": 0},
        map={
            "style": "white-bg",
            "layers": [{
                "below": "traces", "sourcetype": "raster",
                "source": [ACHTERGROND_TILES_URL],
                "sourceattribution": ACHTERGROND_TILES_ATTRIBUTIE,
            }],
            "center": {"lat": float(locaties["lat"].mean()), "lon": float(locaties["lon"].mean())},
            "zoom": 9.5,
        },
        legend={"x": 0.01, "y": 0.99, "bgcolor": "rgba(255,255,255,0.85)", "title": {"text": "Project"}},
        clickmode="event+select",
    )
    return figuur
