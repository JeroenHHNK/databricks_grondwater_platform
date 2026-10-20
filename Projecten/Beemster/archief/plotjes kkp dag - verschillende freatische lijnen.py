# -*- coding: utf-8 -*-
"""
Created on Thu Jun  5 15:04:36 2025

@author: thanzen
"""

from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd

# inlezen grondwater data, buitenwaterstanden, neerslag 1h radar, en coordinaten profiellocaties
excel_base_path = Path("S:/Waterveiligheid_2030/Pilaar 3 - Analyse/03 Exceldocumenten")
data = excel_base_path / "freatische_lijn_formules_beemster.xlsx"
freatische_lijn = pd.read_excel(data)

(dp,) = plt.plot(
    freatische_lijn["x_dijkprofiel"], freatische_lijn["y_dijkprofiel"], color="black"
)
(gw,) = plt.plot(freatische_lijn["x_dgeo"], freatische_lijn["y_dgeo"], color="blue")
(df,) = plt.plot(freatische_lijn["x_bear"], freatische_lijn["y_bear"], color="purple")
(ge,) = plt.plot(
    freatische_lijn["x_gemeten_gem"], freatische_lijn["y_gemeten_gem"], color="green"
)
(lm,) = plt.plot(
    freatische_lijn["x_toetsing"], freatische_lijn["y_toetsing"], color="orange"
)
(lk,) = plt.plot(
    freatische_lijn["x_max_gemeten"], freatische_lijn["y_max_gemeten"], color="red"
)

legend = plt.legend(
    [dp, gw, df, ge, lm, lk],
    [
        "Dijkprofiel",
        "D Geo Flow",
        "Bear",
        "Gemeten gemiddelde",
        "Schematisering HHNK (TRWD)",
        "Max gemeten",
    ],
)
legend.get_frame().set_edgecolor("black")
plt.tight_layout()
plt.xlabel("Afstand [m]")
plt.ylabel("Hoogte [m]")
plt.title("Freatische lijnen dijkprofiel in de Beemster")
plt.grid(True)


# Kleuren in een colormap voor visuele consistentie en toegankelijkheid
kleuren = {
    "Dijkprofiel": "black",
    "D Geo Flow": "#1f77b4",  # blauw
    "Bear": "#9467bd",  # paars
    "Gemeten gemiddelde": "#2ca02c",  # groen
    "Schematisering HHNK (TRWD)": "#ff7f0e",  # oranje
    "Max gemeten": "#d62728",  # rood
}

# Plotten
fig, ax = plt.subplots(figsize=(10, 6))  # Verbeterde grootte

(dp,) = ax.plot(
    freatische_lijn["x_dijkprofiel"],
    freatische_lijn["y_dijkprofiel"],
    color=kleuren["Dijkprofiel"],
    label="Dijkprofiel",
    linewidth=2,
)

(gw,) = ax.plot(
    freatische_lijn["x_dgeo"],
    freatische_lijn["y_dgeo"],
    color=kleuren["D Geo Flow"],
    label="D Geo Flow",
    linestyle="--",
    linewidth=2,
)

(df,) = ax.plot(
    freatische_lijn["x_bear"],
    freatische_lijn["y_bear"],
    color=kleuren["Bear"],
    label="Bear (analytische formule)",
    linestyle="-.",
    linewidth=2,
)

(ge,) = ax.plot(
    freatische_lijn["x_gemeten_gem"],
    freatische_lijn["y_gemeten_gem"],
    color=kleuren["Gemeten gemiddelde"],
    label="Gemeten gemiddelde (feb 23 - mei 25)",
    marker="o",
    markersize=4,
)

(lm,) = ax.plot(
    freatische_lijn["x_toetsing"],
    freatische_lijn["y_toetsing"],
    color=kleuren["Schematisering HHNK (TRWD)"],
    label="Schematisering HHNK (TRWD)",
    linewidth=2,
)

(lk,) = ax.plot(
    freatische_lijn["x_max_gemeten"],
    freatische_lijn["y_max_gemeten"],
    color=kleuren["Max gemeten"],
    label="Max gemeten (feb 23 - mei 25)",
    linewidth=2,
)

# Legenda met rand en transparante achtergrond
legend = ax.legend(frameon=True, edgecolor="black", loc="best", fontsize=9)
legend.get_frame().set_alpha(0.9)

# Layout en assen
ax.set_xlabel("Afstand [m]", fontsize=12)
ax.set_ylabel("Hoogte [m]", fontsize=12)
ax.set_title("Freatische lijnen dijkprofiel in de Beemster", fontsize=14)
ax.grid(True, linestyle=":", alpha=0.7)
plt.tight_layout()
plt.show()
