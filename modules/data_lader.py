# data_lader.py
"""De tijdreeksen naast de stijghoogtes: neerslag en buitenwaterstand."""
import pandas as pd
from pandas import DataFrame
from pathlib import Path


# De naam van de tijdas in de stijghoogtes, zoals de pipeline die sinds de
# peilbuisdata-export (xlsx) gebruikt.
TIJD_KOLOM = "Timestamp"


def lees_tabel(pad: Path, **kwargs) -> DataFrame:
    """Leest een csv of een xlsx (eerste blad), naar de extensie."""
    if Path(pad).suffix.lower() == ".csv":
        return pd.read_csv(pad, float_precision="round_trip", **kwargs)
    return pd.read_excel(pad, **kwargs)


def laad_neerslag_data(neerslag_bestand: Path) -> DataFrame:
    """Laadt neerslaggegevens uit een csv- of Excel-bestand."""
    df = lees_tabel(neerslag_bestand)
    df["Datum"] = pd.to_datetime(df["Datum"])
    df.set_index("Datum", inplace=True)
    return df
