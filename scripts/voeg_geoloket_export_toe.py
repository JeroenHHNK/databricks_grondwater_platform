# -*- coding: utf-8 -*-
"""
Voegt een nieuwe Geoloket-export toe aan de bestaande meetreeks-werkmap.

Oorspronkelijk geschreven op 2026-07-22 door thanzen.

> **Dit is de handmatige route, en de bedoeling is hem kwijt te raken.**
> `notebooks/30_haal_reeksen_en_coordinaten_op.py` haalt dezelfde stijghoogtes
> rechtstreeks uit `gold_prd.hl_grondwaterpeilmeting.meting`. Zolang niet
> vaststaat dat onze peilbuizen daar te vinden zijn (zie
> `notebooks/20_match_peilbuizen.py`), blijft dit script de terugvaloptie.

Werkt per tabblad: neemt uit de nieuwe export alleen de rijen met een
`Timestamp` na de laatste in de moederwerkmap, en plakt die eronder. Alleen
toevoegen dus: herziene historische waarden worden niet gecorrigeerd, en
tabbladen die alleen in de nieuwe export zitten vallen weg.

    uv run python scripts/voeg_geoloket_export_toe.py --project Belmermeer \
        --moeder peilbuisdata_Belmermeer_alle_sensoren_09102026.xlsx \
        --nieuw  peilbuisdata_belmermeer_17112026.xlsx \
        --uit    peilbuisdata_Belmermeer_alle_sensoren_17112026.xlsx
"""

import argparse
import sys
from pathlib import Path
from copy import copy
import pandas as pd
from openpyxl import load_workbook

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from Modules import paths  # noqa: E402

_parser = argparse.ArgumentParser(description=__doc__)
_parser.add_argument("--project", required=True, choices=paths.projecten())
_parser.add_argument("--moeder", required=True, help="bestaande werkmap in peilbuisdata/")
_parser.add_argument("--nieuw", required=True, help="nieuwe Geoloket-export in peilbuisdata/")
_parser.add_argument("--uit", required=True, help="naam van de nieuwe werkmap")
_args = _parser.parse_args()

bestandmap = paths.peilbuisdata_map(_args.project)

moederbestand = bestandmap / _args.moeder
nieuwbestand = bestandmap / _args.nieuw
uitvoerbestand = bestandmap / _args.uit

for _pad in (moederbestand, nieuwbestand):
    if not _pad.is_file():
        raise FileNotFoundError(f"Niet gevonden: {_pad}")
if uitvoerbestand.exists():
    raise FileExistsError(f"Bestaat al, zou overschreven worden: {uitvoerbestand}")

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
