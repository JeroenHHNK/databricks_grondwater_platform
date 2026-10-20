# -*- coding: utf-8 -*-
"""
Created on Wed Jul 22 09:51:52 2026

@author: thanzen
"""


from pathlib import Path
from copy import copy
import pandas as pd
from openpyxl import load_workbook

# --------------------------------------------------
# HARDCODED MAP MET EXCELBESTANDEN
# --------------------------------------------------
bestandmap = Path(r"S:/Waterveiligheid_2030/Pilaar 3 - Analyse/06 Rekenbestanden/freatische_lijn/Projecten/Belmermeer/data")

moederbestand = bestandmap / "peilbuisdata_alle_sensoren_12082026.xlsx"
nieuwbestand = bestandmap / "peilbuisdata_belmermeer_09102026.xlsx"
uitvoerbestand = bestandmap / "peilbuisdata_alle_sensoren_09102026.xlsx"

# ==================================================
# ALLE TABBLADEN INLEZEN
# ==================================================
moeder_sheets = pd.read_excel(
    moederbestand,
    sheet_name=None,
    header=1
)

nieuwe_sheets = pd.read_excel(
    nieuwbestand,
    sheet_name=None,
    header=1
)

resultaat_sheets = {}

for sheetnaam, df_moeder in moeder_sheets.items():

    if sheetnaam not in nieuwe_sheets:
        print(f"Tabblad niet aanwezig in nieuwe dataset: {sheetnaam}")
        resultaat_sheets[sheetnaam] = df_moeder
        continue

    df_nieuw = nieuwe_sheets[sheetnaam]

    # Eventuele spaties in kolomnamen verwijderen
    df_moeder.columns = df_moeder.columns.str.strip()
    df_nieuw.columns = df_nieuw.columns.str.strip()

    # Lege regels verwijderen
    df_moeder = df_moeder.dropna(subset=["Timestamp"])
    df_nieuw = df_nieuw.dropna(subset=["Timestamp"])

    # Laatste timestamp bepalen
    laatste_timestamp = df_moeder["Timestamp"].max()

    # Alleen nieuwe records selecteren
    df_toevoegen = df_nieuw[
        df_nieuw["Timestamp"] > laatste_timestamp
    ]

    # Samenvoegen
    df_resultaat = pd.concat(
        [df_moeder, df_toevoegen],
        ignore_index=True
    )

    resultaat_sheets[sheetnaam] = df_resultaat

    print(
        f"{sheetnaam}: "
        f"{len(df_toevoegen)} records toegevoegd "
        f"(laatste timestamp = {laatste_timestamp})"
    )

# ==================================================
# RESULTAAT WEGSCHRIJVEN
# ==================================================
with pd.ExcelWriter(
    uitvoerbestand,
    engine="openpyxl",
    datetime_format="yyyy-mm-dd hh:mm:ss"
) as writer:

    for sheetnaam, df in resultaat_sheets.items():

        df.to_excel(
            writer,
            sheet_name=sheetnaam,
            index=False,
            startrow=1
        )

# ==================================================
# OPMAAK RIJ 1 EN RIJ 2 OVERNEMEN UIT MOEDERBESTAND
# ==================================================
wb_moeder = load_workbook(moederbestand)
wb_uit = load_workbook(uitvoerbestand)

for sheetnaam in wb_moeder.sheetnames:

    if sheetnaam not in wb_uit.sheetnames:
        continue

    ws_moeder = wb_moeder[sheetnaam]
    ws_uit = wb_uit[sheetnaam]

    # --------------------------------------------------
    # Rij 1 en rij 2 volledig kopiëren
    # --------------------------------------------------
    max_col = ws_moeder.max_column

    for rij in [1, 2]:

        for kolom in range(1, max_col + 1):

            bron = ws_moeder.cell(row=rij, column=kolom)
            doel = ws_uit.cell(row=rij, column=kolom)

            # Waarde kopiëren
            doel.value = bron.value

            # Opmaak kopiëren
            doel.font = copy(bron.font)
            doel.fill = copy(bron.fill)
            doel.border = copy(bron.border)
            doel.alignment = copy(bron.alignment)
            doel.number_format = copy(bron.number_format)
            doel.protection = copy(bron.protection)

    # --------------------------------------------------
    # Kolombreedtes kopiëren
    # --------------------------------------------------
    for kolom_letter, dimensie in ws_moeder.column_dimensions.items():

        ws_uit.column_dimensions[kolom_letter].width = dimensie.width

    # --------------------------------------------------
    # Rijhoogtes kopiëren
    # --------------------------------------------------
    for rij_nr in [1, 2]:

        if rij_nr in ws_moeder.row_dimensions:

            ws_uit.row_dimensions[rij_nr].height = (
                ws_moeder.row_dimensions[rij_nr].height
            )

    # --------------------------------------------------
    # Samengevoegde cellen (merged cells) kopiëren
    # --------------------------------------------------
    for merged_range in ws_moeder.merged_cells.ranges:

        ws_uit.merge_cells(str(merged_range))

# ==================================================
# OPSLAAN
# ==================================================
wb_uit.save(uitvoerbestand)

print("\nKlaar.")
print(f"Bestand opgeslagen als:\n{uitvoerbestand}")
