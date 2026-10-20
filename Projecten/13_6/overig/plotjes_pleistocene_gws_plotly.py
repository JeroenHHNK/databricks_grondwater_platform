# -*- coding: utf-8 -*-
"""
Created on Mon Nov 17 10:05:55 2025

@author: thanzen
"""

from pathlib import Path
import pandas as pd
import plotly.graph_objects as go

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
# Neerslag: uur → dag (som)
neerslag = neerslag.set_index(neerslag.columns[0]).resample("D").sum().reset_index()

# Buitenwater: 15 min → uur (gemiddelde)
buitenwater = (
    buitenwater.set_index(buitenwater.columns[0]).resample("H").mean().reset_index()
)

# vaste maaiveldhoogtes
maaiveld = {
    "MB077PB02": -0.88,
    "MB080PB02": -0.83,
}

datasets = {"MB077PB02": MB077PB02, "MB080PB02": MB080PB02}

# ---- PLOTTEN MET PLOTLY ----
for naam, df in datasets.items():
    fig = go.Figure()

    # Peilbuisdata
    fig.add_trace(
        go.Scatter(
            x=df.iloc[:, 0],
            y=df.iloc[:, 1],
            mode="lines",
            name="Stijghoogte",
            line=dict(color="red", width=2),
        )
    )

    # Buitenwater
    fig.add_trace(
        go.Scatter(
            x=buitenwater.iloc[:, 0],
            y=buitenwater.iloc[:, 1],
            mode="lines",
            name="Buitenwaterstand",
            line=dict(color="blue", width=2),
        )
    )

    # Maaiveld
    fig.add_trace(
        go.Scatter(
            x=[df.iloc[:, 0].min(), df.iloc[:, 0].max()],
            y=[maaiveld[naam], maaiveld[naam]],
            mode="lines",
            name="Maaiveld",
            line=dict(color="black", dash="dash"),
        )
    )

    # Neerslag (rechter y-as)
    fig.add_trace(
        go.Bar(
            x=neerslag.iloc[:, 0],
            y=neerslag.iloc[:, 1],
            name="Neerslag (dag)",
            marker_color="green",
            opacity=0.4,
            yaxis="y2",
        )
    )

    # Layout
    fig.update_layout(
        title=f"Peilbuis {naam}",
        xaxis=dict(title="Datum"),
        yaxis=dict(title="Waterhoogte [m +NAP]"),
        yaxis2=dict(title="Neerslag [mm/dag]", overlaying="y", side="right"),
        bargap=0,
        legend=dict(x=0.01, y=0.99),
        template="plotly_white",
        height=500,
        width=1000,
    )

    # ---- HTML wegschrijven ----
    output_path = excel_base_path / f"{naam}.html"
    fig.write_html(output_path)

    print(f"HTML opgeslagen als: {output_path}")
