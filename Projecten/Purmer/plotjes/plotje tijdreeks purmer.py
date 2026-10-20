# -*- coding: utf-8 -*-
"""
Created on Mon Nov 17 14:37:25 2025

@author: thanzen
"""

from pathlib import Path
import matplotlib.pyplot as plt
import pandas as pd

# -------------------------------------
# BESTANDEN
# -------------------------------------
excel_base_path = Path(
    "S:/Waterveiligheid_2030/Pilaar 3 - Analyse/06 Rekenbestanden/freatische_lijn/Purmer/data"
)

filename_peilbuisdata = excel_base_path / "HB006PB01_17112025.xlsx"
filename_peilbuisdata2 = excel_base_path / "MB007PB01_17112025.xlsx"
filename_peilbuisdata3 = excel_base_path / "MB008PB01_17112025.xlsx"

filename_verdamping = excel_base_path / "verdamping_171125.xlsx"
filename_neerslag = excel_base_path / "neerslag_zeevang_171125.csv"

# -------------------------------------
# INLEZEN
# -------------------------------------
HB006PB01 = pd.read_excel(filename_peilbuisdata)
MB007PB01 = pd.read_excel(filename_peilbuisdata2)
MB008PB01 = pd.read_excel(filename_peilbuisdata3)

neerslag = pd.read_csv(filename_neerslag)
verdamping = pd.read_excel(filename_verdamping)

# Datumkolommen
for df in [HB006PB01, MB007PB01, MB008PB01, neerslag, verdamping]:
    df.iloc[:, 0] = pd.to_datetime(df.iloc[:, 0])

# -------------------------------------
# PLOTTEN
# -------------------------------------

fig, ax1 = plt.subplots(figsize=(14, 6))

ax1.set_xlabel("Datum")
ax1.set_ylabel("Waterhoogte [m +NAP]")

# Kleuren per peilbuis
kleuren = {
    "MB007PB01": "#003F88",  # donker, intens blauw
    "HB006PB01": "#0077B6",  # helder middenblauw
    "MB008PB01": "#00A6FB",  # licht en fel blauw
}

# Plot de drie peilbuizen
ax1.plot(
    MB007PB01.iloc[:, 0],
    MB007PB01.iloc[:, 1],
    color=kleuren["MB007PB01"],
    label="Grondwaterstand buitenkruin",
    linewidth=1.4,
)
ax1.plot(
    HB006PB01.iloc[:, 0],
    HB006PB01.iloc[:, 1],
    color=kleuren["HB006PB01"],
    label="Grondwaterstand binnenberm",
    linewidth=1.4,
)
ax1.plot(
    MB008PB01.iloc[:, 0],
    MB008PB01.iloc[:, 1],
    color=kleuren["MB008PB01"],
    label="Grondwaterstand binnenteen",
    linewidth=1.4,
)

# -------------------------------------
# RECHTER-AS: Neerslag + Verdamping
# -------------------------------------
ax2 = ax1.twinx()
ax2.set_ylabel("Neerslag / Verdamping [mm/dag]")

# Neerslag als bar
ax2.bar(
    neerslag.iloc[:, 0],
    neerslag.iloc[:, 1],
    color="green",
    alpha=0.4,
    width=0.8,
    label="Neerslag",
)

# Verdamping als bar
ax2.bar(
    verdamping.iloc[:, 0],
    verdamping.iloc[:, 1],
    color="orange",
    alpha=0.4,
    width=0.8,
    label="Verdamping",
)

# -------------------------------------
# TITEL & LEGENDA
# -------------------------------------
plt.title("Grondwatermetingen Purmer")

lines_1, labels_1 = ax1.get_legend_handles_labels()
lines_2, labels_2 = ax2.get_legend_handles_labels()

ax1.legend(lines_1 + lines_2, labels_1 + labels_2, loc="upper left")

plt.tight_layout()
plt.show()
