# -*- coding: utf-8 -*-
"""
Created on Thu Jan 23 09:30:27 2025

@author: thanzen
"""

from pathlib import Path
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.patches import Rectangle

# definieer bestandsnamen
excel_base_path = Path("D:/Python/Development/PhreaticLine/Beemster/Data")
filename_dwarsprofielen = excel_base_path / "coordinaten_profielen_beemster.xlsx"
filename_peilbuislocaties = excel_base_path / "locatie_profielen_beemster.xlsx"
filename_peilbuisgegevens = excel_base_path / "DP004_grondwater_beemster_3112025.xlsx"
filename_metadata = excel_base_path / "metadata_slim_meetnetwerk.xlsx"

# inlezen Excel-bestanden
dwarsprofielen = pd.read_excel(filename_dwarsprofielen)
peilbuislocaties = pd.read_excel(filename_peilbuislocaties)
peilbuisgegevens = pd.read_excel(filename_peilbuisgegevens)
metadata = pd.read_excel(filename_metadata)
# plt.plot(dwarsprofielen["X_DP004"], dwarsprofielen["Y_DP004"])

# maak verschillende dataframes per locatie met x,y coordinaten gesplitst in een dataframe
number_of_columns = len(dwarsprofielen.columns)
# split de dataframes in een lijst
coords_dfs = []
# loop over de column index in stappen van 2
for idx in range(0, number_of_columns, 2):
    coords_dfs.append(dwarsprofielen.iloc[:, [idx, idx + 1]])
# Example - dwarsprofiellocatie 4 Beemster
dijkprofielxy = coords_dfs[3].dropna()  # selecteer juiste dijkprofiel op index
dijkprofiel = dijkprofielxy.to_numpy()

# laad peilbuisdata in
stijghoogte_buitenkruin = peilbuisgegevens["HB11_BUKR_F-247"].to_numpy()
stijghoogte_binnenkruin = peilbuisgegevens["MB12_BIKR_F-342"].to_numpy()
stijghoogte_insteek_berm = peilbuisgegevens["HB13_BIB_F-654"].to_numpy()
stijghoogte_binnenteen = peilbuisgegevens["HB14_BIT_F-650"].to_numpy()

# verwijder nan waarden uit reeks
buitenkruin = stijghoogte_buitenkruin[~np.isnan(stijghoogte_buitenkruin)]
binnenkruin = stijghoogte_binnenkruin[~np.isnan(stijghoogte_binnenkruin)]
insteek_berm = stijghoogte_insteek_berm[~np.isnan(stijghoogte_insteek_berm)]
binnenteen = stijghoogte_binnenteen[~np.isnan(stijghoogte_binnenteen)]
stijghoogte_lijst = [buitenkruin, binnenkruin, insteek_berm, binnenteen]

# vul waterstandswaarden in
buitenwaterstand = -0.5
teensloot_peil = -4.6
maatgevend_boezempeil = buitenwaterstand + 0.3

# maak variabele die de peilbuislocatie op het x coordinaat in dwarsprofiel weergeeft
BUKR = peilbuislocaties["X_coordinaat"][11]
BIKR = peilbuislocaties["X_coordinaat"][12]
INST_B = peilbuislocaties["X_coordinaat"][13]
BIT = peilbuislocaties["X_coordinaat"][14]

# selecteer metadata peilbuizen -> gebruik index (naamgeving) om peilbuizen uit raai te selecteren
metadata_pb = metadata.loc[71:74]
metadata_pb = metadata_pb[
    [
        "Code_peilbuis",
        "Hoogte_bovenkant_peilbuis",
        "bovenkant_filter_M_NAP",
        "onderkant_filter_M_NAP",
    ]
].copy()
hoogte_bovenkant_pb = metadata_pb["Hoogte_bovenkant_peilbuis"]
bovenkant_filter = metadata_pb["bovenkant_filter_M_NAP"]
onderkant_filter = metadata_pb["onderkant_filter_M_NAP"]


#########################################################################
# een functie om x- of y-coordinaten te achterhalen (snijpunt met dijkprofiel - gegeven een x of y)
def find_xy_in_dike_profile(profile, value, value_type="y"):
    # Zorg ervoor dat de profielpunten gesorteerd zijn op x-coördinaat
    profile = sorted(profile, key=lambda point: point[0])

    if value_type == "y":
        # Zoek de x-waarden waar de lijn y = 'value' door het profiel snijdt
        intersection_points = []
        for i in range(len(profile) - 1):
            x1, y1 = profile[i]
            x2, y2 = profile[i + 1]

            # Controleer of de lijn y = 'value' tussen y1 en y2 ligt
            if (y1 <= value <= y2) or (y2 <= value <= y1):
                # Bereken het snijpunt op de x-as
                x_intersection = x1 + (x2 - x1) / (y2 - y1) * (value - y1)
                intersection_points.append(x_intersection)

        if not intersection_points:
            raise ValueError("De y-waarde ligt buiten het dijkprofiel")
        return intersection_points

    elif value_type == "x":
        # Zoek de y-waarde bij de gegeven x-waarde door lineaire interpolatie
        for i in range(len(profile) - 1):
            x1, y1 = profile[i]
            x2, y2 = profile[i + 1]

            # Als x1 <= value <= x2, voer de interpolatie uit
            if x1 <= value <= x2:
                y_intersection = y1 + (y2 - y1) / (x2 - x1) * (value - x1)
                return y_intersection

        raise ValueError("De x-waarde ligt buiten het dijkprofiel")

    else:
        raise ValueError("Ongeldige waarde voor 'value_type', kies 'x' of 'y'")


# vind y coordinaat bij BUKR
try:
    y_BUKR = find_xy_in_dike_profile(dijkprofiel, BUKR, value_type="x")
except ValueError as e:
    print(e)
# vind y coordinaat bij BIT
try:
    y_BIT = find_xy_in_dike_profile(dijkprofiel, BIT, value_type="x")
except ValueError as e:
    print(e)
# vind x bij buitenwaterstand
try:
    x_buitenwaterstand = find_xy_in_dike_profile(
        dijkprofiel, buitenwaterstand, value_type="y"
    )
except ValueError as e:
    print(e)
# vind x maatgevend boezempeil
try:
    x_maatgevend_boezempeil = find_xy_in_dike_profile(
        dijkprofiel, maatgevend_boezempeil, value_type="y"
    )
except ValueError as e:
    print(e)
# vind x coordinaaten bij teensloot -> teensloot_peil snijdt veelal op 3 plekken het dijkprofiel
try:
    x_teensloot = find_xy_in_dike_profile(dijkprofiel, teensloot_peil, value_type="y")
except ValueError as e:
    print(e)
# vind y (maaiveld) bij insteek berm
try:
    y_INST_B = find_xy_in_dike_profile(dijkprofiel, INST_B, value_type="x")
except ValueError as e:
    print(e)
# vind y (maaiveld) bij binnenteen
try:
    y_BIT = find_xy_in_dike_profile(dijkprofiel, BIT, value_type="x")
except ValueError as e:
    print(e)

########################################################################################
# gemiddelde waterstand achterhalen voor gebruik in boxplot
gem_buitenkruin = np.mean(buitenkruin)
gem_binnenkruin = np.mean(binnenkruin)
gem_insteek_berm = np.mean(insteek_berm)
gem_binnenteen = np.mean(binnenteen)

# maak een gemiddelde freatische waterstandslijst met x,y: voor plotten van de lijn
gem_waterstand_lijst = pd.DataFrame(
    {
        "x_waterstand": [
            x_buitenwaterstand[0],
            x_buitenwaterstand[4],
            BUKR,
            BIKR,
            INST_B,
            BIT,
            x_teensloot[0],
            x_teensloot[1],
        ],
        "y_waterstand": [
            buitenwaterstand,
            buitenwaterstand,
            gem_buitenkruin,
            gem_binnenkruin,
            gem_insteek_berm,
            gem_binnenteen,
            teensloot_peil,
            teensloot_peil,
        ],
    }
)

# maak een lijst met schematisatie freatische lijn, zoals bij toetsing
# bereken offset schematisatie bij binnenkruin -> lengte kruin is hier bij benadering x coordinaat binnen- en buitenkruin
off = (BIKR - BUKR) * 0.1
schematisatie_binnenkruin = maatgevend_boezempeil - off
# bereken schematisatie insteek berm
schematisatie_insteek_berm = y_INST_B - 0.1
# bereken schematisaite binnenteen
schematisatie_binnenteen = y_BIT - 0.1

schematisatie_freatische_lijn = pd.DataFrame(
    {
        "x_freatisch": [
            x_maatgevend_boezempeil[0],
            x_maatgevend_boezempeil[1],
            BUKR,
            BIKR,
            INST_B,
            BIT,
            x_teensloot[0],
            x_teensloot[1],
        ],
        "y_freatisch": [
            maatgevend_boezempeil,
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

#################################################################################################################
# plot figuur met afstand op 2e x-as en schematisatie freatische lijn uit toetsing
fig, ax1 = plt.subplots(figsize=(10, 8))
meanpointprops = dict(marker="D", markeredgecolor="black", markerfacecolor="orange")
labels = ["Buitenkruin", "Binnenkruin", "Insteek berm", "Binnenteen"]
# Filterdieptes peilbuis visualiseren
for idx, locatie in enumerate(peilbuislocaties["X_coordinaat"][11:15]):
    hoogte_bovenkant = hoogte_bovenkant_pb.iloc[idx]
    filter_top = bovenkant_filter.iloc[idx]
    filter_bottom = onderkant_filter.iloc[idx]
    filter_breedte = 1
    # 1. De verticale lijn voor de peilbuis van maaiveld naar de bovenkant van het filter
    ax1.vlines(locatie, hoogte_bovenkant, filter_top, color="grey", lw=2)
    # 2. De smalle, langwerpige rechthoek van de bovenkant filter tot de onderkant filter
    filter_rectangle = Rectangle(
        (locatie - filter_breedte / 2, filter_bottom),
        filter_breedte,
        filter_top - filter_bottom,
        fill=False,
        edgecolor="grey",
        linewidth=2,
    )
    # Voeg de rechthoek toe aan de plot
    ax1.add_patch(filter_rectangle)
    # ax1.scatter(locatie, hoogte_bovenkant, color='red', zorder=5)
bp = ax1.boxplot(
    stijghoogte_lijst,
    tick_labels=labels,
    positions=[BUKR, BIKR, INST_B, BIT],
    patch_artist=True,
    widths=3.5,
    showmeans=True,
    meanprops=meanpointprops,
    flierprops=dict(markeredgecolor="red"),
    medianprops=dict(color="red"),
    boxprops=dict(facecolor="lightblue", color="black"),
)
ax1.set_ylabel("Hoogte m NAP", fontsize=13)
ax1.set_xticklabels(labels, rotation=20, fontsize=13)
ax1.tick_params(axis="x", labelsize=12)
ax1.tick_params(axis="y", labelsize=12)
# ax1.set_ylim([-7.5,7.5])
ax1.plot(
    dijkprofielxy["X_DP004"],
    dijkprofielxy["Y_DP004"],
    color="black",
    linestyle="-",
    linewidth=2.2,
)
(gw,) = ax1.plot(
    gem_waterstand_lijst["x_waterstand"],
    gem_waterstand_lijst["y_waterstand"],
    color="lightblue",
    linestyle="-",
    linewidth=1.8,
)
(fl,) = ax1.plot(
    schematisatie_freatische_lijn["x_freatisch"],
    schematisatie_freatische_lijn["y_freatisch"],
    color="blue",
    linestyle="-",
    linewidth=1.8,
)
legend = ax1.legend(
    [bp["medians"][0], bp["means"][0], bp["boxes"][0], bp["fliers"][0], gw, fl],
    [
        "Mediaan",
        "Gemiddelde",
        "Kwartiel 1 tot 3 (50% data)",
        "Uitschieters",
        "Gemiddelde freatische lijn",
        "Schematisatie freatische lijn",
    ],
    loc="best",
)
legend.get_frame().set_edgecolor("black")
ax1.grid(True)
ax1.set_title("Grondwaterstand Beemster profiel 4", fontsize=17, pad=30)
ax2 = ax1.twiny()
ax2.plot(
    dijkprofielxy["X_DP004"],
    dijkprofielxy["Y_DP004"],
    color="black",
    linestyle="-",
    linewidth=2.2,
)
ax2.set_xlabel("Afstand (meter)", fontsize=13, loc="center", labelpad=8)
ax2.tick_params(axis="x", labelsize=12)
plt.tight_layout()
plt.show()
