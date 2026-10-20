# -*- coding: utf-8 -*-
"""
Created on Mon Nov 17 16:01:59 2025

@author: thanzen
"""

from pathlib import Path
import pandas as pd
import plotly.graph_objects as go

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
# PLOTTEN MET PLOTLY
# -------------------------------------

# Maak de Plotly figuur aan
fig = go.Figure()

# Kleuren per peilbuis
kleuren = {
    "MB007PB01": "#003F88",  # donker, intens blauw
    "HB006PB01": "#0077B6",  # helder middenblauw
    "MB008PB01": "#00A6FB",  # licht en fel blauw
}

# Plot de drie peilbuizen als lijnplots
fig.add_trace(
    go.Scatter(
        x=MB007PB01.iloc[:, 0],
        y=MB007PB01.iloc[:, 1],
        mode="lines",
        name="Grondwaterstand buitenkruin",
        line=dict(color=kleuren["MB007PB01"], width=1.4),
    )
)
fig.add_trace(
    go.Scatter(
        x=HB006PB01.iloc[:, 0],
        y=HB006PB01.iloc[:, 1],
        mode="lines",
        name="Grondwaterstand binnenberm",
        line=dict(color=kleuren["HB006PB01"], width=1.4),
    )
)
fig.add_trace(
    go.Scatter(
        x=MB008PB01.iloc[:, 0],
        y=MB008PB01.iloc[:, 1],
        mode="lines",
        name="Grondwaterstand binnenteen",
        line=dict(color=kleuren["MB008PB01"], width=1.4),
    )
)

# Voeg de neerslag en verdamping toe als barplots op de secundaire y-as
fig.add_trace(
    go.Bar(
        x=neerslag.iloc[:, 0],
        y=neerslag.iloc[:, 1],
        name="Neerslag",
        marker=dict(color="green", opacity=0.4),
        yaxis="y2",
    )
)

fig.add_trace(
    go.Bar(
        x=verdamping.iloc[:, 0],
        y=verdamping.iloc[:, 1],
        name="Verdamping",
        marker=dict(color="orange", opacity=0.4),
        yaxis="y2",
    )
)

# -------------------------------------
# TITEL, AS LABELS, LEGEND, Y-AS
# -------------------------------------
fig.update_layout(
    title="Grondwatermetingen Purmer",
    xaxis_title="Datum",
    yaxis_title="Waterhoogte [m +NAP]",
    barmode="stack",  # Zorgt dat de bar charts gestapeld worden
    yaxis2=dict(
        title="Neerslag / Verdamping [mm/dag]",
        overlaying="y",  # Maakt de tweede y-as over de eerste
        side="right",
        showgrid=False,  # Verberg de grid op de secundaire y-as
    ),
    legend=dict(x=0.1, y=0.9),
    template="plotly_white",
    width=1000,
    height=600,
)

# Sla de figuur op als een .html bestand
fig.write_html(excel_base_path / "grondwatermetingen_purmer.html")

# Toon de figuur in de browser
fig.show()
