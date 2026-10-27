# data_lader.py
import pandas as pd
from pandas import DataFrame, Series
from pathlib import Path
import traceback
from typing import IO


# De twee kolommen van een peilbuisreeks, zoals in de peilbuisdata-export en in
# de csv per peilbuis die scripts/zet_export_om.py daaruit maakt.
TIJD_KOLOM = "Timestamp"
STIJGHOOGTE_KOLOM = "Waterniveau (m NAP)"


def lees_tabel(pad: Path, **kwargs) -> DataFrame:
    """Leest een csv of een xlsx (eerste blad), naar de extensie."""
    if Path(pad).suffix.lower() == ".csv":
        return pd.read_csv(pad, float_precision="round_trip", **kwargs)
    return pd.read_excel(pad, **kwargs)


def lees_export_reeksen(
    peilbuisdata_bestand: Path | IO[bytes], stijghoogte_namen: list[str]
) -> dict[str, Series]:
    """
    De reeksen uit een peilbuisdata-export (xlsx, één blad per peilbuis), per naam.

    Het blad wordt op deelstring gezocht en de eerste treffer wint; zie de README.
    Namen zonder (bruikbaar) blad ontbreken in het resultaat.
    """
    xlsx = pd.ExcelFile(peilbuisdata_bestand, engine="openpyxl")
    stijghoogte_data: dict[str, Series] = {}

    for naam in stijghoogte_namen:
        overeenkomende_bladen = [blad for blad in xlsx.sheet_names if naam in blad]

        if overeenkomende_bladen:
            blad = overeenkomende_bladen[0]
            print(f"✅ Blad gevonden voor '{naam}': {blad}")
            df = xlsx.parse(blad, header=1)
            vereiste_kolommen = [TIJD_KOLOM, STIJGHOOGTE_KOLOM]

            if all(col in df.columns for col in vereiste_kolommen):
                df = df[vereiste_kolommen].dropna(subset=vereiste_kolommen)
                df[TIJD_KOLOM] = pd.to_datetime(df[TIJD_KOLOM], errors="coerce")
                df = df.dropna(subset=[TIJD_KOLOM])
                df = df.set_index(TIJD_KOLOM)
                df = df[~df.index.duplicated(keep="first")]
                stijghoogte_data[naam] = df[STIJGHOOGTE_KOLOM]
            else:
                print(f"⚠️ Blad '{blad}' mist vereiste kolommen: {df.columns}")
        else:
            print(f"⚠️ Geen blad gevonden voor '{naam}'")
    return stijghoogte_data


def laad_peilbuis_data(
    peilbuisdata_bestand: Path | IO[bytes], stijghoogte_namen: list[str]
) -> DataFrame:
    """
    Laadt tijdreeksgegevens voor opgegeven peilbuizen uit een peilbuisdata-export.

    `peilbuisdata_bestand` mag een pad zijn of een bestandsobject, zoals een
    upload in de Streamlit-app; er wordt dan niets naar schijf geschreven.
    """
    return _combineer(lees_export_reeksen(peilbuisdata_bestand, stijghoogte_namen), stijghoogte_namen)


def schrijf_reeks_csv(reeks: Series, pad: Path) -> None:
    """Eén peilbuisreeks als csv met de kolommen TIJD_KOLOM en STIJGHOOGTE_KOLOM."""
    pad.parent.mkdir(parents=True, exist_ok=True)
    reeks.rename(STIJGHOOGTE_KOLOM).rename_axis(TIJD_KOLOM).to_csv(pad)


def laad_peilbuis_csv(reeksen_map: Path, stijghoogte_namen: list[str]) -> DataFrame:
    """
    Zelfde resultaat als `laad_peilbuis_data`, uit één csv per peilbuis:
    `<reeksen_map>/<peilbuis_naam>.csv`.
    """
    stijghoogte_data: dict[str, Series] = {}
    for naam in stijghoogte_namen:
        pad: Path = reeksen_map / f"{naam}.csv"
        if not pad.is_file():
            print(f"⚠️ Geen reeks gevonden voor '{naam}': {pad.name}")
            continue
        df = pd.read_csv(pad, parse_dates=[TIJD_KOLOM], index_col=TIJD_KOLOM, float_precision="round_trip")
        stijghoogte_data[naam] = df[STIJGHOOGTE_KOLOM]
    return _combineer(stijghoogte_data, stijghoogte_namen)


def _combineer(stijghoogte_data: dict[str, Series], stijghoogte_namen: list[str]) -> DataFrame:
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


def laad_neerslag_data(neerslag_bestand: Path) -> DataFrame:
    """Laadt neerslaggegevens uit een csv- of Excel-bestand."""
    df = lees_tabel(neerslag_bestand)
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
        df = lees_tabel(terugkeertijden_bestand, index_col=0)
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
