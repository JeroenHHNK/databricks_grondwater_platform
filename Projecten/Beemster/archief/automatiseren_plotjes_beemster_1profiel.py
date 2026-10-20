# -*- coding: utf-8 -*-
"""
Created on Fri Jul 11 09:01:20 2025

@author: thanzen
"""

from pathlib import Path

import matplotlib.pyplot as plt
import matplotlib.ticker as ticker
import numpy as np
import pandas as pd
from matplotlib.patches import Rectangle

# === Config en data inladen ===
base_path = Path(
    "S:/Waterveiligheid_2030/Pilaar 3 - Analyse/06 Rekenbestanden/freatische_lijn/Beemster/Data"
)
config_path = base_path / "config_profielen.xlsx"

# Inlezen config
general_config = pd.read_excel(
    config_path, sheet_name="general_config", index_col="key"
)["value"].to_dict()
profielen = pd.read_excel(config_path, sheet_name="profielen")

# Kies profiel, bijvoorbeeld eerste regel
profiel = profielen.iloc[3]

coords_sheet = profiel["coords_sheet"]
coords_df = pd.read_excel(config_path, sheet_name=coords_sheet).dropna()

#####################################################################################
# automatisch inladen peilbuisdata uit gegeven excelsheet downloadportaal wiertsema

# Pad naar je ruwe data (bijvoorbeeld de nieuw gedownloade file)
ruwe_data_path = base_path / "peilbuisdata_alle_sensoren_11072025.xlsx"

# Lijst van stijghoogte_namen uit je profiel
stijghoogte_namen = profiel["stijghoogte_namen"].split(",")

# Laad eerst alle sheetnamen in de Excel
xls = pd.ExcelFile(ruwe_data_path, engine="openpyxl")
alle_sheetnamen = xls.sheet_names

# === Per peilbuis zoeken naar bijbehorende sheet en inlezen ===
stijghoogte_data = {}

for naam in stijghoogte_namen:
    matching_sheets = [sheet for sheet in alle_sheetnamen if naam in sheet]
    if matching_sheets:
        sheet = matching_sheets[0]
        print(f"✅ Sheet gevonden voor '{naam}': {sheet}")

        df = pd.read_excel(ruwe_data_path, sheet_name=sheet, header=1)
        required_cols = ["Timestamp", "Waterniveau (m NAP)"]

        if all(col in df.columns for col in required_cols):
            df = df[required_cols].dropna(subset=["Timestamp", "Waterniveau (m NAP)"])
            df["Timestamp"] = pd.to_datetime(df["Timestamp"], errors="coerce")
            df = df.dropna(subset=["Timestamp"])
            df = df.set_index("Timestamp")
            df = df[~df.index.duplicated(keep="first")]
            stijghoogte_data[naam] = df["Waterniveau (m NAP)"]
        else:
            print(f"⚠️ Sheet '{sheet}' mist vereiste kolommen: {df.columns}")
    else:
        print(f"⚠️ Geen sheet gevonden voor '{naam}'")

# === Combineer tot één DataFrame ===
if stijghoogte_data:
    peilbuisgegevens = pd.concat(stijghoogte_data.values(), axis=1)
    peilbuisgegevens.columns = stijghoogte_data.keys()
    print("\n✅ Data succesvol gecombineerd tot 'peilbuisgegevens':")
    print(peilbuisgegevens.head())
else:
    raise ValueError("Geen geldige peilbuisdata gevonden om samen te voegen!")

#############################################################################################
# -> de indice uit deze excelsheet wordt gebruikt voor config bestand
filename_metadata = base_path / "metadata_slim_meetnetwerk_15072025.xlsx"
metadata = pd.read_excel(filename_metadata)

# === Basis parameters ===
buitenwaterstand = general_config["buitenwaterstand"]
teensloot_peil = profiel["teensloot_peil"]
maatgevend_boezempeil = (
    buitenwaterstand + general_config["maatgevend_boezempeil_offset"]
)

# === Data voorbereiden ===
stijghoogte_namen = profiel["stijghoogte_namen"].split(",")
peilbuis_idxs = list(map(int, profiel["peilbuis_indices"].split(",")))
metadata_pb_idxs = list(map(int, profiel["metadata_pb_indices"].split(",")))

# Maak arrays van stijghoogte
stijghoogte_lijst = []
for naam in stijghoogte_namen:
    data = peilbuisgegevens[naam].dropna().to_numpy()
    stijghoogte_lijst.append(data)

# Metadata peilbuizen
metadata_pb = metadata.loc[metadata_pb_idxs]
metadata_pb = metadata_pb[
    [
        "Code_peilbuis",
        "Hoogte_bovenkant_peilbuis",
        "bovenkant_filter_M_NAP",
        "onderkant_filter_M_NAP",
    ]
]

# X-coördinaten van peilbuizen
peilbuislocaties = pd.read_excel(base_path / "locatie_profielen_beemster.xlsx")
peilbuis_x = peilbuislocaties.loc[peilbuis_idxs, "X_coordinaat"].to_list()

# Dijkprofiel
dijkprofiel = coords_df[["x", "y"]].to_numpy()


# === Functie snijpunten vinden ===
def find_xy_in_dike_profile(profile, value, value_type="y"):
    profile = sorted(profile, key=lambda p: p[0])
    if value_type == "y":
        result = []
        for i in range(len(profile) - 1):
            x1, y1 = profile[i]
            x2, y2 = profile[i + 1]
            if (y1 <= value <= y2) or (y2 <= value <= y1):
                x_cross = x1 + (x2 - x1) / (y2 - y1) * (value - y1)
                result.append(x_cross)
        return result
    elif value_type == "x":
        for i in range(len(profile) - 1):
            x1, y1 = profile[i]
            x2, y2 = profile[i + 1]
            if x1 <= value <= x2:
                y_cross = y1 + (y2 - y1) / (x2 - x1) * (value - x1)
                return y_cross
    return None


# === Vind snijpunten ===
x_buitenwaterstand = find_xy_in_dike_profile(dijkprofiel, buitenwaterstand, "y")
x_teensloot = find_xy_in_dike_profile(dijkprofiel, teensloot_peil, "y")
x_maatgevend_boezempeil = find_xy_in_dike_profile(
    dijkprofiel, maatgevend_boezempeil, "y"
)
y_peilbuis_maaiveld = [find_xy_in_dike_profile(dijkprofiel, x, "x") for x in peilbuis_x]

# === Gemiddelde waterstanden ===
gem = [np.mean(data) for data in stijghoogte_lijst]

# === Robuust opbouwen waterstandslijnen ===
min_peilbuizen = 4
voldoende_peilbuizen = len(peilbuis_x) >= min_peilbuizen
voldoende_snijpunten = x_buitenwaterstand and x_teensloot and x_maatgevend_boezempeil

if voldoende_peilbuizen and voldoende_snijpunten:
    print(
        f"✅ Genoeg peilbuizen ({len(peilbuis_x)}) en snijpunten gevonden → schematisatie_freatische_lijn wordt gemaakt."
    )

    bukr_x = peilbuis_x[0]
    x_bw_near_bukr = min(x_buitenwaterstand, key=lambda x: abs(x - bukr_x))

    x_ts_max = max(x_teensloot)
    x_ts_bit = max(peilbuis_x)
    x_ts_min = min(x_teensloot, key=lambda x: abs(x - x_ts_bit))

    # Gebruik altijd het begin van het dijkprofiel als start van de buitenwaterlijn
    dijkprofiel_start_x = dijkprofiel[0][0]

    gem_waterstand_lijst = pd.DataFrame(
        {
            "x": [dijkprofiel_start_x, x_bw_near_bukr]
            + peilbuis_x
            + [x_ts_min, x_ts_max],
            "y": [buitenwaterstand, buitenwaterstand]
            + gem
            + [teensloot_peil, teensloot_peil],
        }
    )

    schematisatie_insteek_berm = y_peilbuis_maaiveld[2] - 0.1
    schematisatie_binnenteen = y_peilbuis_maaiveld[3] - 0.1
    schematisatie_binnenkruin = (
        maatgevend_boezempeil - (peilbuis_x[1] - peilbuis_x[0]) * 0.1
    )

    schematisatie_freatische_lijn = pd.DataFrame(
        {
            "x": [dijkprofiel_start_x] + peilbuis_x + [x_ts_min, x_ts_max],
            "y": [
                maatgevend_boezempeil,
                maatgevend_boezempeil,
                schematisatie_binnenkruin,
                schematisatie_insteek_berm,
                schematisatie_binnenteen,
                teensloot_peil,
                teensloot_peil,
            ],
        }
    )
    plot_schematisatie = True

else:
    print(
        "⚠️ Niet genoeg peilbuizen of ontbrekende snijpunten → schematisatie_freatische_lijn wordt NIET gemaakt."
    )
    if voldoende_snijpunten:
        bukr_x = peilbuis_x[0]
        x_bw_near_bukr = min(x_buitenwaterstand, key=lambda x: abs(x - bukr_x))

        x_ts_max = max(x_teensloot)
        x_ts_bit = max(peilbuis_x)
        x_ts_min = min(x_teensloot, key=lambda x: abs(x - x_ts_bit))

        dijkprofiel_start_x = dijkprofiel[0][0]

        gem_waterstand_lijst = pd.DataFrame(
            {
                "x": [dijkprofiel_start_x, x_bw_near_bukr]
                + peilbuis_x
                + [x_ts_min, x_ts_max],
                "y": [buitenwaterstand, buitenwaterstand]
                + gem
                + [teensloot_peil, teensloot_peil],
            }
        )
    else:
        gem_waterstand_lijst = pd.DataFrame({"x": peilbuis_x, "y": gem})
    plot_schematisatie = False

####################################################################################################
# plot de boxplot
# --- Stijl & kleuren ---
meanpointprops = dict(
    marker="D", markeredgecolor="black", markerfacecolor="#E69F00"
)  # warm oranje
medianprops = dict(color="#CC79A7", linewidth=2.5)  # fel roze/paars
box_facecolor = "lightblue"
box_edgecolor = "black"
flierprops = dict(markeredgecolor="#D55E00")  # warm rood/oranje

# --- Labels en posities ---
labels = metadata_pb["Code_peilbuis"].tolist()
positions = peilbuis_x

# --- Figuur & as ---
fig, ax1 = plt.subplots(figsize=(12, 9))

# --- Filterdieptes tekenen ---
filter_breedte = 1
for idx, locatie in enumerate(positions):
    hoogte_bovenkant = metadata_pb.iloc[idx]["Hoogte_bovenkant_peilbuis"]
    filter_top = metadata_pb.iloc[idx]["bovenkant_filter_M_NAP"]
    filter_bottom = metadata_pb.iloc[idx]["onderkant_filter_M_NAP"]

    ax1.vlines(locatie, hoogte_bovenkant, filter_top, color="grey", lw=2)
    filter_rectangle = Rectangle(
        (locatie - filter_breedte / 2, filter_bottom),
        filter_breedte,
        filter_top - filter_bottom,
        fill=False,
        edgecolor="grey",
        linewidth=2,
    )
    ax1.add_patch(filter_rectangle)

# --- Boxplot ---
bp = ax1.boxplot(
    stijghoogte_lijst,
    tick_labels=labels,
    positions=positions,
    patch_artist=True,
    widths=3,
    showmeans=True,
    meanprops=meanpointprops,
    medianprops=medianprops,
    flierprops=flierprops,
    boxprops=dict(facecolor=box_facecolor, color=box_edgecolor),
)

# --- Voeg laatst gemeten waterstanden toe ---
# Gebruik ffill om evt. NaNs op te vullen, daarna laatste waarde pakken
laatste_waardes = peilbuisgegevens.ffill().iloc[-1]

# Plot deze als rode stippen op de juiste posities
lm = ax1.scatter(
    positions,
    laatste_waardes.values,
    color="red",
    edgecolors="black",
    linewidths=1.2,
    zorder=3,
)

# --- Waterstandslijnen ---
(gw,) = ax1.plot(
    gem_waterstand_lijst["x"],
    gem_waterstand_lijst["y"],
    color="#0072B2",
    linestyle="-",
    linewidth=2,
    label="Gemiddelde freatische lijn",
)

# Plot schematisatie alleen als hij bestaat
if plot_schematisatie:
    (fl,) = ax1.plot(
        schematisatie_freatische_lijn["x"],
        schematisatie_freatische_lijn["y"],
        color="black",
        linestyle="--",
        linewidth=2,
        label="Schematische freatische lijn",
    )
else:
    fl = None

# --- Dijkprofiel ---
ax1.plot(
    dijkprofiel[:, 0], dijkprofiel[:, 1], color="black", linestyle="-", linewidth=2.2
)

# --- Y-as limieten robuust ---
all_filter_bottoms = metadata_pb["onderkant_filter_M_NAP"].tolist()
y_min = min(all_filter_bottoms) - 0.5
y_max = max(max([max(l) for l in stijghoogte_lijst]), max(dijkprofiel[:, 1])) + 0.5
ax1.set_ylim(y_min, y_max)

# --- Labels, ticks, legend ---
ax1.set_ylabel("Hoogte [m NAP]", fontsize=13)
ax1.yaxis.set_major_locator(ticker.MultipleLocator(1))
ax1.set_xticklabels(labels, rotation=20, fontsize=12)
ax1.tick_params(axis="x", labelsize=12)
ax1.tick_params(axis="y", labelsize=12)

# achterhaal begindatum en einddatum metingen peilbuizen
start_datum = peilbuisgegevens.index.min().strftime("%d-%m-%Y")
eind_datum = peilbuisgegevens.index.max().strftime("%d-%m-%Y")

legend_items = [
    bp["medians"][0],
    bp["means"][0],
    bp["boxes"][0],
    bp["fliers"][0],
    gw,
    lm,
]
legend_labels = [
    "Mediaan",
    "Gemiddelde",
    "Kwartiel 1–3 (50% data)",
    "Uitschieters",
    "Gemiddelde freatische lijn",
    f"Laatste meetpunt ({eind_datum})",
]

if plot_schematisatie:
    legend_items.insert(-1, fl)
    legend_labels.insert(-1, "Schematische freatische lijn")

legend = ax1.legend(legend_items, legend_labels, loc="best")
legend.get_frame().set_edgecolor("black")
ax1.grid(True, linestyle="dashed", alpha=1)

# --- Titel met datumrange ---

ax1.set_title(
    f"Grondwaterstand Beemster {profiel['naam']} (data van {start_datum} t/m {eind_datum})",
    fontsize=17,
    pad=20,
)

# --- Extra x-as bovenin ---
ax2 = ax1.twiny()
ax2.plot(
    dijkprofiel[:, 0], dijkprofiel[:, 1], color="black", linestyle="-", linewidth=2.2
)
ax2.set_xlabel("Afstand [m]", fontsize=13, labelpad=8)
ax2.tick_params(axis="x", labelsize=12)

plt.tight_layout()
plt.show()
