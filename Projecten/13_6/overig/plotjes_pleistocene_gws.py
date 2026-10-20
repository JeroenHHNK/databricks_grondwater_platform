# -*- coding: utf-8 -*-
"""
Created on Mon Nov 17 09:01:19 2025

@author: thanzen
"""

from pathlib import Path
import matplotlib.pyplot as plt
import pandas as pd

# definieer bestandsnamen
excel_base_path = Path(
    "S:/Waterveiligheid_2030/Pilaar 3 - Analyse/06 Rekenbestanden/freatische_lijn/13_6"
)
filename_peilbuisdata = excel_base_path / "peilbuisdata_pleistoceen_13_6_171125.xlsx"
filename_peilbuisdata2 = (
    excel_base_path / "peilbuisdata_pleistoceen_MB080PB02_13_6_171125.xlsx"
)
filename_buitenwater = (
    excel_base_path / "waterstanden_vier_noorder_koggen_uitstroom_171125.csv"
)
filename_neerslag = excel_base_path / "neerslag_vier_noorder_koggen_171125.csv"

# inlezen
MB077PB02 = pd.read_excel(filename_peilbuisdata)
MB080PB02 = pd.read_excel(filename_peilbuisdata2)

neerslag = pd.read_csv(filename_neerslag)
buitenwater = pd.read_csv(filename_buitenwater)

# ---- TIJDKOLOMMEN NAAR DATETIME ----
for df in [MB077PB02, MB080PB02, buitenwater, neerslag]:
    df.iloc[:, 0] = pd.to_datetime(df.iloc[:, 0])

# ---- RESAMPLE ----
# Neerslag: van uur → dag (som)
neerslag = neerslag.set_index(neerslag.columns[0]).resample("D").sum().reset_index()

# Buitenwater: van 15 min → uur (gemiddelde)
buitenwater = (
    buitenwater.set_index(buitenwater.columns[0]).resample("H").mean().reset_index()
)

# vaste maaiveldhoogtes
maaiveld = {
    "MB077PB02": -0.88,
    "MB080PB02": -0.83,
}

# datasets
datasets = {"MB077PB02": MB077PB02, "MB080PB02": MB080PB02}

# ---- PLOTTEN ----
for naam, df in datasets.items():
    fig, ax1 = plt.subplots(figsize=(12, 5))

    ax1.set_xlabel("Datum")
    ax1.set_ylabel("Waterhoogte [m +NAP]", color="black")

    # Peilbuisdata
    ax1.plot(
        df.iloc[:, 0], df.iloc[:, 1], color="red", label="Stijghoogte", linewidth=1.8
    )

    # Buitenwater
    ax1.plot(
        buitenwater.iloc[:, 0],
        buitenwater.iloc[:, 1],
        color="blue",
        label="Buitenwaterstand",
        linewidth=1.4,
    )

    # Maaiveld
    ax1.axhline(
        maaiveld[naam], color="black", linestyle="--", linewidth=1, label="Maaiveld"
    )

    # rechter y-as: neerslag
    ax2 = ax1.twinx()
    ax2.set_ylabel("Neerslag [mm/dag]", color="green")

    ax2.bar(
        neerslag.iloc[:, 0],
        neerslag.iloc[:, 1],
        color="green",
        alpha=0.4,
        width=0.8,
        label="Neerslag (dag)",
    )

    # Titel en legenda
    plt.title(f"Peilbuis {naam}")
    lines_1, labels_1 = ax1.get_legend_handles_labels()
    lines_2, labels_2 = ax2.get_legend_handles_labels()
    ax1.legend(lines_1 + lines_2, labels_1 + labels_2, loc="upper left")

    plt.tight_layout()
    plt.show()
