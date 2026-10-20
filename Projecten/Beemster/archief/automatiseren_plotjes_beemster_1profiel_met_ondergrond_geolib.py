# -*- coding: utf-8 -*-
"""
Created on Fri Jul 18 13:37:31 2025

@author: thanzen
@co-author: kstelling
"""

import re
from collections import defaultdict
from pathlib import Path

import folium
import numpy as np
import pandas as pd
import plotly.graph_objects as go
import plotly.io as pio
import pyproj
import yaml
from geolib.models import DStabilityModel
from shapely.geometry import Polygon

pio.renderers.default = "browser"

# === Bestands- en sheet-referenties configureren ===
with open("config/config.yaml", "r") as f:
    yaml_config = yaml.safe_load(f)


# Alle paden, sheets en kleuren centraal bovenin
BASE_PATH = Path(yaml_config["base_data_dir"])
CONFIG_FILE = yaml_config["config_file"]
CONFIG_SHEETS = yaml_config["config_sheets"]
PEILBUISDATA_FILE = yaml_config["peilbuisdata_file"]
METADATA_FILE = yaml_config["metadata_file"]
PROFIELLOCATIES_FILE = yaml_config["locatie_profielen_file"]
# Nieuw: map met stix-bestanden
STIX_FOLDER = yaml_config.get("stix_folder", "stix_files")  # Voeg toe aan config.yaml
STIX_FOLDER_PATH = BASE_PATH / STIX_FOLDER


# === Config en data inladen ===
def configure(base_path: Path, config: str, sheets: list[str]):
    config_path = base_path / config

    df_config = pd.read_excel(config_path, sheet_name=sheets[0], index_col="key")
    df_profielen = pd.read_excel(config_path, sheet_name=sheets[1])
    general_config = df_config["value"].to_dict()

    return general_config, df_profielen


# === Functies snijpunten vinden ===
def find_y_in_cross_section(profile, value):
    profile = sorted(profile, key=lambda p: p[0])

    for i in range(len(profile) - 1):
        x1, y1 = profile[i]
        x2, y2 = profile[i + 1]
        if x1 <= value <= x2:
            y_cross = y1 + (y2 - y1) / (x2 - x1) * (value - x1)
            return y_cross


def find_x_in_cross_section(profile, value):
    profile = sorted(profile, key=lambda p: p[0])
    result = []

    for i in range(len(profile) - 1):
        x1, y1 = profile[i]
        x2, y2 = profile[i + 1]
        if (y1 <= value <= y2) or (y2 <= value <= y1):
            x_cross = x1 + (x2 - x1) / (y2 - y1) * (value - y1)
            result.append(x_cross)
    return result


# --- Functies voor laden en voorbereiden van data ---
def load_profiel_data(base_path, config_file, profielen, index):
    profiel = profielen.iloc[index]
    coords_sheet = profiel["coords_sheet"]
    coords_df = pd.read_excel(base_path / config_file, sheet_name=coords_sheet)
    coords_df.dropna()
    return profiel, coords_df


def load_peilbuis_data(peilbuisdata_file, stijghoogte_namen):
    xlsx = pd.ExcelFile(peilbuisdata_file, engine="openpyxl")
    stijghoogte_data = {}

    for naam in stijghoogte_namen:
        matching_sheets = [sheet for sheet in xlsx.sheet_names if naam in sheet]

        if matching_sheets:
            sheet = matching_sheets[0]
            print(f"✅ Sheet gevonden voor '{naam}': {sheet}")
            df = pd.read_excel(peilbuisdata_file, sheet_name=sheet, header=1)
            required_cols = ["Timestamp", "Waterniveau (m NAP)"]

            if all(col in df.columns for col in required_cols):
                df = df[required_cols].dropna(
                    subset=["Timestamp", "Waterniveau (m NAP)"]
                )
                df["Timestamp"] = pd.to_datetime(df["Timestamp"], errors="coerce")
                df = df.dropna(subset=["Timestamp"])
                df = df.set_index("Timestamp")
                df = df[~df.index.duplicated(keep="first")]
                stijghoogte_data[naam] = df["Waterniveau (m NAP)"]
            else:
                print(f"⚠️ Sheet '{sheet}' mist vereiste kolommen: {df.columns}")
        else:
            print(f"⚠️ Geen sheet gevonden voor '{naam}'")
    if stijghoogte_data:
        peilbuisgegevens = pd.concat(stijghoogte_data.values(), axis=1)
        peilbuisgegevens.columns = stijghoogte_data.keys()
        print("✅ Data succesvol gecombineerd tot 'peilbuisgegevens':")
        print(peilbuisgegevens.head())
        return peilbuisgegevens
    else:
        raise ValueError("⚠️ Geen geldige peilbuisdata gevonden om samen te voegen!")


def load_peilbuislocaties(peilbuislocaties_file, peilbuis_idxs):
    peilbuislocaties = pd.read_excel(peilbuislocaties_file)
    x_peilbuis = peilbuislocaties.loc[peilbuis_idxs, "X_coordinaat"].to_list()
    return x_peilbuis


def prepare_metadata_pb(metadata, metadata_pb_idxs):
    metadata_pb = metadata.loc[metadata_pb_idxs]
    metadata_pb = metadata_pb[
        [
            "Code_peilbuis",
            "Hoogte_bovenkant_peilbuis",
            "bovenkant_filter_M_NAP",
            "onderkant_filter_M_NAP",
        ]
    ]
    return metadata_pb


def gem_waterstand_df(
    x_dijkprofiel_start,
    x_bw_near_bukr,
    x_peilbuis,
    x_ts_min,
    x_ts_max,
    gem,
    buitenwaterstand,
    teensloot_peil,
):
    x_vals = [x_dijkprofiel_start, x_bw_near_bukr] + x_peilbuis + [x_ts_min, x_ts_max]
    y_vals = 2 * [buitenwaterstand] + gem + 2 * [teensloot_peil]
    return pd.DataFrame({"x": x_vals, "y": y_vals})


def calc_waterstand_lijnen(
    dijkprofiel,
    buitenwaterstand,
    maatgevend_boezempeil,
    teensloot_peil,
    x_peilbuis,
    y_peilbuis_mv,
    gemiddelden,
):
    x_buitenwaterstand = find_x_in_cross_section(dijkprofiel, buitenwaterstand)
    x_boezempeil = find_x_in_cross_section(dijkprofiel, maatgevend_boezempeil)
    x_teensloot = find_x_in_cross_section(dijkprofiel, teensloot_peil)
    voldoende_snijpunten = x_buitenwaterstand and x_teensloot and x_boezempeil

    try:
        x_bukr = x_peilbuis[0]
    except IndexError:
        x_bukr = dijkprofiel[0][0]

    x_bw_bij_bukr = (
        min(x_buitenwaterstand, key=lambda x: abs(x - x_bukr))
        if x_buitenwaterstand
        else dijkprofiel[0][0]
    )
    x_ts_min = (
        min(x_teensloot, key=lambda x: abs(x - max(x_peilbuis)))
        if x_teensloot
        else dijkprofiel[-1][0]
    )
    x_ts_max = max(x_teensloot) if x_teensloot else dijkprofiel[-1][0]
    x_dijkprofiel_start = dijkprofiel[0][0]

    df_gem_waterstand = gem_waterstand_df(
        x_dijkprofiel_start,
        x_bw_bij_bukr,
        x_peilbuis,
        x_ts_min,
        x_ts_max,
        gemiddelden,
        buitenwaterstand,
        teensloot_peil,
    )

    if len(x_peilbuis) >= 4 and voldoende_snijpunten and len(y_peilbuis_mv) >= 4:
        fr_insteek_berm = y_peilbuis_mv[2] - 0.1
        fr_binnenteen = y_peilbuis_mv[3] - 0.1
        fr_binnenkruin = maatgevend_boezempeil - (x_peilbuis[1] - x_peilbuis[0]) * 0.1
        xs_freatische_lijn = [x_dijkprofiel_start] + x_peilbuis + [x_ts_min, x_ts_max]
        ys_freatische_lijn = (
            2 * [maatgevend_boezempeil]
            + [fr_binnenkruin]
            + [fr_insteek_berm]
            + [fr_binnenteen]
            + 2 * [teensloot_peil]
        )
        freatische_lijn = pd.DataFrame(
            {"x": xs_freatische_lijn, "y": ys_freatische_lijn}
        )
        plot_schematisatie = True
    else:
        freatische_lijn = None
        plot_schematisatie = False

    return df_gem_waterstand, freatische_lijn, plot_schematisatie


# --- Main script ---


def get_soil_layer_properties(soil_layer, stix_data, index=0):
    geometry = None
    soil_code = None
    soil_color = None

    for layer in stix_data.geometries[index].Layers:
        if layer.Id == soil_layer.LayerId:
            geometry = layer
            break
    for soil in stix_data.soils.Soils:
        if soil.Id == soil_layer.SoilId:
            soil_code = soil.Code
            break
    for visu in stix_data.soilvisualizations.SoilVisualizations:
        if visu.SoilId == soil_layer.SoilId:
            soil_color = visu.Color
            break
    return geometry, soil_code, soil_color


def argb_to_rgb(argb_hex: str) -> str:
    if argb_hex.startswith("#") and len(argb_hex) == 9:
        return "#" + argb_hex[3:]
    elif argb_hex.startswith("#") and len(argb_hex) == 7:
        return argb_hex
    else:
        raise ValueError("⚠️ Ongeldig (A)RGB hex format. Verwachte format: #(AA)RRGGBB")


ruwe_data_path = BASE_PATH / PEILBUISDATA_FILE
config, profielen = configure(BASE_PATH, CONFIG_FILE, CONFIG_SHEETS)
metadata = pd.read_excel(BASE_PATH / METADATA_FILE)

stix_files = sorted(list(STIX_FOLDER_PATH.glob("*.stix")))
if not stix_files:
    print(f"⚠️ Geen stix-bestanden gevonden in map: {STIX_FOLDER_PATH}")

for stix_file in stix_files:
    print(f"--- Verwerk stix-bestand: {stix_file.name} ---")

    # Reset de figure en soil data voor elke iteratie
    fig = go.Figure()
    geometry_by_soil = defaultdict(lambda: {"color": None, "polygons": []})

    # --- Profielkeuze op basis van stix bestandsnaam ---
    match = re.search(r"DWP(\d+)", stix_file.stem)
    if not match:
        print(f"⚠️ Kon profielnummer niet vinden in {stix_file.name}. Overslaan.")
        continue

    profiel_nr = int(match.group(1))
    profiel_naam_in_config = f"Profiel {profiel_nr}"
    profiel_rij = profielen[profielen["naam"] == profiel_naam_in_config]

    if profiel_rij.empty:
        print(
            f"⚠️ Profiel '{profiel_naam_in_config}' niet gevonden in configuratie. Overslaan."
        )
        continue

    # Gebruik de eerste gevonden rij (er zou er maar één moeten zijn)
    profiel = profiel_rij.iloc[0]
    # De index voor load_profiel_data is de index van de rij in de `profielen` dataframe
    profiel_index = profiel_rij.index[0]

    print(f"✅ Profiel gevonden: {profiel['naam']} (Index: {profiel_index})")

    # --- Data laden voor dit profiel ---
    try:
        _, coords_df = load_profiel_data(
            BASE_PATH, CONFIG_FILE, profielen, profiel_index
        )
        metadata_pb_idx = list(map(int, profiel["metadata_pb_indices"].split(",")))
        metadata_pb = prepare_metadata_pb(metadata, metadata_pb_idx)

        dijkprofiel = coords_df[["x", "y"]].to_numpy()
        buitenwaterstand = config["buitenwaterstand"]
        maatgevend_boezempeil = (
            buitenwaterstand + config["maatgevend_boezempeil_offset"]
        )
        teensloot_peil = profiel["teensloot_peil"]

        pb_idxs = list(map(int, profiel["peilbuis_indices"].split(",")))
        x_peilbuis = load_peilbuislocaties(BASE_PATH / PROFIELLOCATIES_FILE, pb_idxs)
        y_peilbuis_mv = [find_y_in_cross_section(dijkprofiel, x) for x in x_peilbuis]

        stijghoogte_namen = profiel["stijghoogte_namen"].split(",")
        pb_gegevens = load_peilbuis_data(ruwe_data_path, stijghoogte_namen)
        stijghoogtes = [
            pb_gegevens[naam].dropna().to_numpy() for naam in stijghoogte_namen
        ]
        gemiddelden = [np.mean(data) for data in stijghoogtes]

        gem_waterstanden, freatische_lijn, plot_schematisatie = calc_waterstand_lijnen(
            dijkprofiel,
            buitenwaterstand,
            maatgevend_boezempeil,
            teensloot_peil,
            x_peilbuis,
            y_peilbuis_mv,
            gemiddelden,
        )
    except Exception as e:
        print(f"❌ Fout bij laden van data voor {profiel['naam']}: {e}")
        continue  # Ga naar het volgende stix-bestand

    # --- Verwerk STIX data voor ondergrond ---
    try:
        dm = DStabilityModel()
        dm.parse(stix_file)
        stix_data = dm.datastructure
        for soil_layer in stix_data.soillayers[0].SoilLayers:
            geometry, soil_code, soil_color = get_soil_layer_properties(
                soil_layer, stix_data
            )
            if geometry is None or soil_code is None or soil_color is None:
                continue
            if geometry_by_soil[soil_code]["color"] is None:
                geometry_by_soil[soil_code]["color"] = soil_color
            coords = [(pt.X, pt.Z) for pt in geometry.Points]
            try:
                polygon = Polygon(coords)
                if polygon.is_valid:
                    geometry_by_soil[soil_code]["polygons"].append(polygon)
            except Exception:
                pass  # Skip invalid polygons
    except Exception as e:
        print(f"⚠️ Fout bij verwerken van {stix_file.name}: {e}")
        continue

    # --- Plotly figuur & traces ---
    # (fig en geometry_by_soil worden nu aan het begin van de loop gereset)

    # Voeg ondergrondvlakken toe
    legend_labels_done = set()
    for soil, colorgon in geometry_by_soil.items():
        polygons = colorgon["polygons"]
        kleur = argb_to_rgb(colorgon["color"])
        toon_label = soil not in legend_labels_done
        for poly in polygons:
            x, y = poly.exterior.xy
            fig.add_trace(
                go.Scatter(
                    x=list(x),
                    y=list(y),
                    fill="toself",
                    mode="lines",
                    line=dict(color=kleur, width=0.5),
                    fillcolor=kleur,
                    opacity=0.8,
                    name=soil if toon_label else None,
                    showlegend=toon_label,
                    legendgroup=soil,
                )
            )
            toon_label = False
        legend_labels_done.add(soil)

    # Plotkleuren uit config
    PLOT_COLORS = yaml_config.get("plot_colors", {})
    BOX_EDGE_COLOR = PLOT_COLORS.get("box_edge", "black")
    BOX_FACE_COLOR = PLOT_COLORS.get("box_face", "lightblue")
    FLIER_EDGE_COLOR = PLOT_COLORS.get("flier_edge", "#D55E00")
    GW_LINE_COLOR = PLOT_COLORS.get("gw_line", "#0072B2")
    LAST_POINT_COLOR = PLOT_COLORS.get("last_point", "red")
    LAST_POINT_EDGE = PLOT_COLORS.get("last_point_edge", "black")
    MEANPOINT_COLOR = PLOT_COLORS.get("meanpoint", "#E69F00")
    MEDIAN_COLOR = PLOT_COLORS.get("median", "#CC79A7")

    # --- Boxplot, mean, median, fliers, laatste meetpunt ---
    labels = metadata_pb["Code_peilbuis"].tolist()
    positions = x_peilbuis
    filter_breedte = 1

    mean_x, mean_y, median_x, median_y, outlier_x, outlier_y = [], [], [], [], [], []
    for idx, (label, pos, data) in enumerate(zip(labels, positions, stijghoogtes)):
        q1, q3 = np.percentile(data, [25, 75])
        iqr = q3 - q1
        lower_fence, upper_fence = q1 - 1.5 * iqr, q3 + 1.5 * iqr
        outliers = data[(data < lower_fence) | (data > upper_fence)]

        fig.add_trace(
            go.Box(
                y=data,
                x=[pos] * len(data),
                name="Boxplot (50% data)",
                boxpoints=False,
                marker=dict(color=BOX_FACE_COLOR, line=dict(color=BOX_EDGE_COLOR)),
                line=dict(color=BOX_EDGE_COLOR),
                fillcolor=BOX_FACE_COLOR,
                boxmean=False,
                width=3,
                showlegend=(idx == 0),
                legendgroup="boxplot",
            )
        )
        mean_x.append(pos)
        mean_y.append(float(np.mean(data)))
        median_x.append(pos)
        median_y.append(float(np.median(data)))
        outlier_x.extend([pos] * len(outliers))
        outlier_y.extend(outliers.tolist())

    if outlier_x:
        fig.add_trace(
            go.Scatter(
                x=outlier_x,
                y=outlier_y,
                mode="markers",
                marker=dict(
                    symbol="circle-open",
                    color=FLIER_EDGE_COLOR,
                    size=6,
                    line=dict(width=2),
                ),
                name="Uitschieters",
                legendgroup="outliers",
                showlegend=True,
            )
        )

    # --- Filterdieptes tekenen ---
    for idx, locatie in enumerate(positions):
        filter_top = metadata_pb.iloc[idx]["bovenkant_filter_M_NAP"]
        filter_bottom = metadata_pb.iloc[idx]["onderkant_filter_M_NAP"]
        hoogte_bovenkant = metadata_pb.iloc[idx]["Hoogte_bovenkant_peilbuis"]
        fig.add_trace(
            go.Scatter(
                x=[locatie, locatie],
                y=[hoogte_bovenkant, filter_top],
                mode="lines",
                line=dict(color="grey", width=2),
                showlegend=False,
            )
        )
        fig.add_shape(
            type="rect",
            x0=locatie - filter_breedte / 2,
            x1=locatie + filter_breedte / 2,
            y0=filter_bottom,
            y1=filter_top,
            line=dict(color="grey", width=2),
            fillcolor="rgba(0,0,0,0)",
            layer="above",
        )

    # --- Waterstandslijnen ---
    fig.add_trace(
        go.Scatter(
            x=gem_waterstanden["x"],
            y=gem_waterstanden["y"],
            mode="lines",
            line=dict(color=GW_LINE_COLOR, width=2),
            name="Gemiddelde freatische lijn",
            legendgroup="gw_lijn",
        )
    )
    if plot_schematisatie and freatische_lijn is not None:
        fig.add_trace(
            go.Scatter(
                x=freatische_lijn["x"],
                y=freatische_lijn["y"],
                mode="lines",
                line=dict(color="black", width=2, dash="dash"),
                name="Schematische freatische lijn",
                legendgroup="schematische_lijn",
            )
        )

    # --- Dijkprofiel ---
    fig.add_trace(
        go.Scatter(
            x=dijkprofiel[:, 0],
            y=dijkprofiel[:, 1],
            mode="lines",
            line=dict(color="black", width=2.2),
            name="Dijkprofiel",
            legendgroup="dijkprofiel",
            showlegend=False,
        )
    )

    # --- Markers voor mediaan, gemiddelde en laatste meetpunt ---
    fig.add_trace(
        go.Scatter(
            x=median_x,
            y=median_y,
            mode="markers",
            marker=dict(
                symbol="line-ew-open", color=MEDIAN_COLOR, size=18, line=dict(width=4)
            ),
            name="Mediaan",
            legendgroup="mediaan",
        )
    )
    fig.add_trace(
        go.Scatter(
            x=mean_x,
            y=mean_y,
            mode="markers",
            marker=dict(
                symbol="diamond",
                color=MEANPOINT_COLOR,
                size=6,
                line=dict(color=LAST_POINT_EDGE, width=1.5),
            ),
            name="Gemiddelde",
            legendgroup="gemiddelde",
        )
    )
    laatste_waardes = pb_gegevens.ffill().iloc[-1]
    fig.add_trace(
        go.Scatter(
            x=positions,
            y=laatste_waardes.values,
            mode="markers",
            marker=dict(
                color=LAST_POINT_COLOR,
                line=dict(color=LAST_POINT_EDGE, width=1.2),
                size=6,
                symbol="circle",
            ),
            name=f"Laatste meetpunt ({pb_gegevens.index.max().strftime('%d-%m-%Y %H:%M')})",
            legendgroup="laatste_meetpunt",
        )
    )
    gw_x, gw_y = gem_waterstanden["x"].tolist(), gem_waterstanden["y"].tolist()
    ext_x = gw_x[:2] + list(positions) + gw_x[-2:]
    ext_y = gw_y[:2] + list(laatste_waardes.values) + gw_y[-2:]
    fig.add_trace(
        go.Scatter(
            x=ext_x,
            y=ext_y,
            mode="lines",
            line=dict(color=LAST_POINT_COLOR, width=2, dash="dot"),
            name=f"Laatste meetlijn ({pb_gegevens.index.max().strftime('%d-%m-%Y %H:%M')})",
            legendgroup="laatste_meetpunt_lijn",
        )
    )

    # --- Layout en opslaan ---
    y_min = min(metadata_pb["onderkant_filter_M_NAP"].tolist()) - 0.5
    y_max = (
        max(
            max(
                [
                    max(stijghoogte)
                    for stijghoogte in stijghoogtes
                    if len(stijghoogte) > 0
                ]
            ),
            max(dijkprofiel[:, 1]),
        )
        + 0.5
    )
    start_datum = pb_gegevens.index.min().strftime("%d-%m-%Y")
    eind_datum = pb_gegevens.index.max().strftime("%d-%m-%Y")

    fig.update_layout(
        title=dict(
            text=f"Grondwaterstand Beemster {profiel['naam']}<br><span style='font-size:12px'>(Data van {start_datum} t/m {eind_datum})</span>",
            x=0.5,
        ),
        yaxis=dict(
            title="Hoogte [m+NAP]",
            range=[y_min, y_max],
            dtick=1,
            gridcolor="rgba(0,0,0,0.15)",
        ),
        xaxis=dict(
            title="Peilbuis positie [m]",
            tickvals=positions,
            ticktext=labels,
            tickangle=-15,
            gridcolor="rgba(0,0,0,0.15)",
        ),
        xaxis2=dict(
            overlaying="x",
            side="top",
            title="Afstand [m]",
            tickmode="array",
            tickvals=dijkprofiel[:, 0].tolist(),
            ticktext=[f"{int(x)}" for x in dijkprofiel[:, 0]],
            showgrid=False,
        ),
        legend=dict(
            bordercolor="black",
            borderwidth=1,
            orientation="v",
            x=1.01,
            y=1,
            xanchor="left",
            yanchor="top",
        ),
        margin=dict(l=60, r=250, t=80, b=60),
        width=1200,
        height=700,
        plot_bgcolor="#FAFAFA",
    )

    # Sla de HTML op met een naam gebaseerd op het stix-bestand
    output_filename = f"plots/plot_beemster_{stix_file.stem}.html"
    fig.write_html(output_filename)
    print(f"✅ Plot opgeslagen als: {output_filename}\n")


# --- Folium kaart met markers per profiel ---
profielen_df = pd.read_excel(BASE_PATH / CONFIG_FILE, sheet_name=CONFIG_SHEETS[1])
stix_files_map = {
    f"Profiel {int(re.search(r'DWP(\d+)', f.stem).group(1))}": f
    for f in stix_files
    if re.search(r"DWP(\d+)", f.stem)
}

profiel_locaties = []
for idx, profiel_row in profielen_df.iterrows():
    profiel_naam = profiel_row["naam"]
    stix_file_for_profiel = stix_files_map.get(profiel_naam)

    if not stix_file_for_profiel:
        continue

    if pd.notna(profiel_row.get("x")) and pd.notna(profiel_row.get("y")):
        x_rd, y_rd = profiel_row["x"], profiel_row["y"]
        transformer = pyproj.Transformer.from_crs(
            "EPSG:28992", "EPSG:4326", always_xy=True
        )
        lon, lat = transformer.transform(x_rd, y_rd)

        plot_html = f"plot_beemster_{stix_file_for_profiel.stem}.html"
        profiel_locaties.append(
            {"naam": profiel_naam, "lat": lat, "lon": lon, "html": plot_html}
        )

if profiel_locaties:
    mean_lat = np.mean([loc["lat"] for loc in profiel_locaties])
    mean_lon = np.mean([loc["lon"] for loc in profiel_locaties])
    m = folium.Map(location=[mean_lat, mean_lon], zoom_start=12)

    for locatie in profiel_locaties:
        # Controleer of het HTML-bestand daadwerkelijk bestaat
        plot_path = Path("plots") / locatie["html"]
        if plot_path.exists():
            popup_html = f"<b>{locatie['naam']}</b><br><a href='{locatie['html']}' target='_blank'>Bekijk plot</a>"
        else:
            popup_html = f"<b>{locatie['naam']}</b><br>Plot niet beschikbaar"

        folium.Marker(
            location=[locatie["lat"], locatie["lon"]],
            popup=folium.Popup(popup_html, max_width=300),
            tooltip=locatie["naam"],
            icon=folium.Icon(color="blue", icon="info-sign"),
        ).add_to(m)

    html_map_path = Path("plots/kaart_profielen_met_plotly.html")
    m.save(str(html_map_path))
    print(f"✅ HTML kaart opgeslagen als: {html_map_path}")
else:
    print("⚠️ Geen profiel locaties met coördinaten gevonden voor folium kaart.")
