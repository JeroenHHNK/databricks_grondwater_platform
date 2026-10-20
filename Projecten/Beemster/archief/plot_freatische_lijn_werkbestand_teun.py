# -*- coding: utf-8 -*-
"""
Created on Thu Jan 23 09:30:27 2025

@author: thanzen
"""

from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

# definieer bestandsnamen
excel_base_path = Path("D:/Python/Development/PhreaticLine/Excel")
filename_dwarsprofielen = excel_base_path / "coordinaten_profielen_beemster.xlsx"
filename_peilbuislocaties = excel_base_path / "locatie_profielen_beemster.xlsx"
filename_peilbuisgegevens = excel_base_path / "DP004_grondwater_beemster_3112025.xlsx"

# inlezen Excel-bestanden
dwarsprofielen = pd.read_excel(filename_dwarsprofielen)
peilbuislocaties = pd.read_excel(filename_peilbuislocaties)
peilbuisgegevens = pd.read_excel(filename_peilbuisgegevens)
plt.plot(dwarsprofielen["X_DP004"], dwarsprofielen["Y_DP004"])

# maak verschillende dataframes per locatie met x,y coordinaten gesplitst in een dataframe
number_of_columns = len(dwarsprofielen.columns)
# split de dataframes in een lijst
coords_dfs = []
# loop over de column index in stappen van 2
for i in range(0, number_of_columns, 2):
    coords_dfs.append(dwarsprofielen.iloc[:, [i, i + 1]])
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

# vul waterstandswaardes in
buitenwaterstand = -0.5
teensloot_peil = -4.6

# maak variabele die de peilbuislocatie op het x coordinaat in dwarsprofiel weergeeft
BUKR = peilbuislocaties["X_coordinaat"][11]
BIKR = peilbuislocaties["X_coordinaat"][12]
INST_B = peilbuislocaties["X_coordinaat"][13]
BIT = peilbuislocaties["X_coordinaat"][14]


#########################################################################
# schrijf een functie om x-,y coordinaten te achterhalen (snijpunt dijkprofiel)
def find_xy_in_dike_profile(profile, value, value_type="y"):
    """
    Vind de corresponderende x-coördinaten bij een gegeven y-coördinaat (horizontale lijn)
    of de corresponderende y-waarde bij een gegeven x-coördinaat (verticale lijn).
    :param profile: List van tuples, waar elk tuple (x, y) een punt in het profiel is
    :param value: De waarde waarvoor je de corresponderende x- of y-coördinaat zoekt
    :param value_type: 'y' als je x-waarden zoekt bij een gegeven y, of 'x' als je y-waarde zoekt bij een gegeven x
    :return: Een lijst van corresponderende x-waarden (snijpunten) of een enkele y-waarde
    :raises ValueError: Als de waarde buiten de grenzen van het profiel ligt
    """
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
try:
    x_buitenwaterstand = find_xy_in_dike_profile(
        dijkprofiel, buitenwaterstand, value_type="y"
    )
except ValueError as e:
    print(e)
# vind x coordinaaten bij teensloot -> teensloot_peil snijdt veelal op 3 plekken het dijkprofiel
try:
    x_teensloot = find_xy_in_dike_profile(dijkprofiel, teensloot_peil, value_type="y")
except ValueError as e:
    print(e)
# vind y coordinaat bij BIKR
# try:
#    y_BIKR = find_xy_in_dike_profile(dijkprofiel, BIKR, value_type='x')
# except ValueError as e:
#    print(e)
# vind y coordinaat bij INST_B
# try:
#    y_INST_B = find_xy_in_dike_profile(dijkprofiel, INST_B, value_type='x')
# except ValueError as e:
#    print(e)
# vind x coordinaat bij buitenwaterstand

###################################################################################
# dit is een voorbeeldje -> maak een dataframe om buitenwaterstand te plotten als lijn
# buitenwaterstand_lijn = pd.DataFrame({'x_buitenwaterstand': [0, x_buitenwaterstand[0]],
#                                      'y_buitenwaterstand': [buitenwaterstand, buitenwaterstand]})
# teensloot_lijn = pd.DataFrame({'x_teensloot': [x_teensloot[1], x_teensloot[2]],
#                                      'y_teensloot': [teensloot_peil, teensloot_peil]})
# plot dijkprofiel
# plt.plot(dijkprofielxy['X_DP001'], dijkprofielxy['Y_DP001'], color='black', linestyle='-')
# plot buitenwaterstandslijn
# plt.plot(buitenwaterstand_lijn['x_buitenwaterstand'],
#         buitenwaterstand_lijn['y_buitenwaterstand'], color="red", linestyle='--')
# plt.plot(teensloot_lijn['x_teensloot'],
#         teensloot_lijn['y_teensloot'], color="blue", linestyle='--')
# plot locatie peilbuis als stip
# plt.plot(BUKR, y_BUKR, 'ro')
# plt.plot(BIT, y_BIT, 'ro')

########################################################################################
# gemiddelde waterstand achterhalen
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
# plot figuur
plt.figure(figsize=(10, 8))
meanpointprops = dict(marker="D", markeredgecolor="black", markerfacecolor="orange")
labels = ["Buitenkruin", "Binnenkruin", "Insteek berm", "Binnenteen"]
bp = plt.boxplot(
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
plt.ylabel("hoogte m NAP", fontsize=14)
plt.xticks(rotation=20, fontsize=14)
plt.plot(
    dijkprofielxy["X_DP004"],
    dijkprofielxy["Y_DP004"],
    color="black",
    linestyle="-",
    linewidth=2.2,
)
plt.plot(
    gem_waterstand_lijst["x_waterstand"],
    gem_waterstand_lijst["y_waterstand"],
    color="blue",
    linestyle="-",
    linewidth=1.8,
)  # marker="o",
legend = plt.legend(
    [bp["medians"][0], bp["means"][0], bp["boxes"][0], bp["fliers"][0]],
    ["Mediaan", "Gemiddelde", "Kwartiel 1 tot 3", "Uitschieters"],
)
legend.get_frame().set_edgecolor("black")
plt.grid(True)
plt.title("Grondwaterstand Beemster profiel 4", fontsize=15)
plt.tight_layout()
plt.show()
