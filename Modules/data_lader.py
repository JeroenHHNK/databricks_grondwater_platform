# data_lader.py
import pandas as pd
from pandas import DataFrame, Series
from pathlib import Path
import traceback


def laad_profiel_data(
    basis_pad: Path, config_bestand: str, profielen: DataFrame, index: int
) -> tuple[Series, DataFrame]:
    """Laadt de coördinaatgegevens voor een specifiek profiel."""
    profiel = profielen.iloc[index]
    coords_blad = profiel["coords_sheet"]
    coords_df = pd.read_excel(basis_pad / config_bestand, sheet_name=coords_blad)

    # Sommige bladen bevatten x/y-waarden als tekst met vaste (non-breaking) spaties
    # erachter (bijv. '0.00\xa0\xa0\xa0'), waardoor de kolom als tekst i.p.v. getal
    # wordt ingelezen. Dwing daarom expliciet naar numeriek af.
    for kolom in ("x", "y"):
        if kolom in coords_df.columns:
            coords_df[kolom] = pd.to_numeric(
                coords_df[kolom].astype(str).str.strip().str.replace(",", ".", regex=False),
                errors="coerce",
            )

    coords_df.dropna(inplace=True)
    return profiel, coords_df


def laad_peilbuis_data(
    peilbuisdata_bestand: Path, stijghoogte_namen: list[str]
) -> DataFrame:
    """Laadt tijdreeksgegevens voor opgegeven peilbuizen."""
    xlsx = pd.ExcelFile(peilbuisdata_bestand, engine="openpyxl")
    stijghoogte_data = {}

    for naam in stijghoogte_namen:
        overeenkomende_bladen = [blad for blad in xlsx.sheet_names if naam in blad]

        if overeenkomende_bladen:
            blad = overeenkomende_bladen[0]
            print(f"✅ Blad gevonden voor '{naam}': {blad}")
            df = pd.read_excel(peilbuisdata_bestand, sheet_name=blad, header=1)
            vereiste_kolommen = ["Timestamp", "Waterniveau (m NAP)"]

            if all(col in df.columns for col in vereiste_kolommen):
                df = df[vereiste_kolommen].dropna(
                    subset=["Timestamp", "Waterniveau (m NAP)"]
                )
                df["Timestamp"] = pd.to_datetime(df["Timestamp"], errors="coerce")
                df = df.dropna(subset=["Timestamp"])
                df = df.set_index("Timestamp")
                df = df[~df.index.duplicated(keep="first")]
                stijghoogte_data[naam] = df["Waterniveau (m NAP)"]
            else:
                print(f"⚠️ Blad '{blad}' mist vereiste kolommen: {df.columns}")
        else:
            print(f"⚠️ Geen blad gevonden voor '{naam}'")

    ontbrekende_namen = [naam for naam in stijghoogte_namen if naam not in stijghoogte_data]
    if ontbrekende_namen:
        raise ValueError(
            f"⚠️ Geen (bruikbare) data gevonden voor peilbuis/peilbuizen: {ontbrekende_namen}. "
            "Controleer of het bijbehorende blad in het peilbuisdata-bestand bestaat."
        )

    if stijghoogte_data:
        peilbuisgegevens = pd.concat(stijghoogte_data.values(), axis=1)
        peilbuisgegevens.columns = stijghoogte_data.keys()
        print("✅ Data succesvol gecombineerd tot 'peilbuisgegevens'.")
        return peilbuisgegevens
    else:
        raise ValueError("⚠️ Geen geldige peilbuisdata gevonden om samen te voegen!")


def bereid_metadata_peilbuis_voor(metadata: DataFrame, metadata_pb_indices: list[int]):
    """Bereidt de metadata voor en filtert deze voor de relevante peilbuizen."""
    metadata_pb = metadata.loc[metadata_pb_indices]
    metadata_pb = metadata_pb[
        [
            "Code_peilbuis",
            "Hoogte_bovenkant_peilbuis",
            "bovenkant_filter_M_NAP",
            "onderkant_filter_M_NAP",
        ]
    ]
    return metadata_pb


def laad_neerslag_data(neerslag_bestand: Path) -> DataFrame:
    """Laadt neerslaggegevens uit een Excel-bestand."""
    df = pd.read_excel(neerslag_bestand)
    df["Datum"] = pd.to_datetime(df["Datum"])
    df.set_index("Datum", inplace=True)
    return df


def laad_terugkeertijden_data(terugkeertijden_bestand: Path) -> DataFrame | None:
    """
    Laadt gegevens over terugkeertijden uit een Excel-bestand.

    De 'Return period'-rij in het Excel-bestand bevat waarden zoals '1_nat',
    '100_droog', etc. (of, voor oudere bestanden, enkel '1', '100', ... zonder
    nat/droog-suffix). Na het transponeren krijgt de resulterende DataFrame een
    MultiIndex (terugkeertijd, type) zodat natte en droge periodes met dezelfde
    terugkeertijd los van elkaar herkenbaar blijven.
    """
    if not terugkeertijden_bestand.exists():
        print(f"⚠️ Terugkeertijden-bestand niet gevonden: {terugkeertijden_bestand}")
        return None
    try:
        df = pd.read_excel(terugkeertijden_bestand, index_col=0)
        df = df.transpose()

        originele_index = df.index.astype(str)
        onderdelen = originele_index.str.extract(
            r"(?P<terugkeertijd>\d+)(?:_(?P<type>\w+))?", expand=True
        )

        terugkeertijd = pd.to_numeric(onderdelen["terugkeertijd"], errors="coerce")
        # Bestanden zonder nat/droog-suffix (oude indeling) worden als 'nat' behandeld.
        type_kolom = onderdelen["type"].fillna("nat").str.lower()
        type_kolom = type_kolom.where(type_kolom.isin(["nat", "droog"]), "nat")

        geldig = terugkeertijd.notna()
        df = df[geldig.to_numpy()]
        df.index = pd.MultiIndex.from_arrays(
            [terugkeertijd[geldig].astype(int), type_kolom[geldig]],
            names=["terugkeertijd", "type"],
        )
        df = df.sort_index()

        print(
            f"✅ Terugkeertijden-bestand succesvol geladen en getransponeerd: {terugkeertijden_bestand}"
        )
        return df
    except Exception as e:
        print(f"❌ Fout bij het laden van terugkeertijden-bestand: {e}")
        traceback.print_exc()
        return None
