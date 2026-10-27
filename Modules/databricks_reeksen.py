# databricks_reeksen.py
"""
Stijghoogtes uit Databricks, per peilbuis één meetreeks.

Een `meetlocatie_id` in `config.csv` kan meer dan één meetreeks hebben. Dat zijn
geen opvolgende loggers maar parallelle sensoren met vaak heel andere niveaus
(Purmer HB012PB01: gemiddeld -2,23, -0,00 en -5,36 m NAP), dus samenvoegen kan
niet. De keuze, in deze volgorde:

1. alleen reeksen in `m NAP` met minstens één meting;
2. staat er een `meetreeks_id` in `config.csv`, dan die (vastgezet);
3. anders de reeks met de meeste metingen.

Voor de MVP zijn het de ruwe metingen: er wordt niet op `validatie_status`
gefilterd. De waarden zijn daggemiddelden (UTC-dag).

Bron nu: de csv's die `notebooks/db_notebook_exporteer_stijghoogtes.py` in
`input_data/databricks_export/` schrijft. Een live query op het datawarehouse
kan dezelfde `kies_reeksen` gebruiken.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
from pandas import DataFrame, Series

from . import paths, workbook_lader
from .data_lader import TIJD_KOLOM

EENHEID: str = "m NAP"
REEKSEN_CSV: str = "reeksen.csv"
DAG_CSV: str = "stijghoogtes_dag.csv"

# Waarom een peilbuis wel of geen reeks krijgt; ook gebruikt door scripts/controleer_reeksen.py.
VASTGEZET = "vastgezet"
ENIGE = "enige reeks"
MEESTE = "meeste metingen"
GEEN_ID = "geen meetlocatie_id"
GEEN_REEKS = "geen meetreeks op deze meetlocatie"
GEEN_NAP = f"geen reeks in {EENHEID} met metingen"
VASTGEZET_ONBEKEND = "vastgezette meetreeks_id hoort niet bij deze meetlocatie"


def export_map() -> Path:
    return paths.invoer_basis() / "databricks_export"


def export_beschikbaar() -> bool:
    return (export_map() / REEKSEN_CSV).is_file() and (export_map() / DAG_CSV).is_file()


def lees_reeksen() -> DataFrame:
    """`reeksen.csv`: per peilbuis per meetreeks, met `reeks_nr`, eenheid en aantal metingen."""
    return pd.read_csv(export_map() / REEKSEN_CSV, dtype={"meetreeks_id": str, "meetlocatie_id": str})


def kies_reeksen(config: DataFrame, reeksen: DataFrame) -> DataFrame:
    """
    Per rij van `config` (project, peilbuis, meetlocatie_id, meetreeks_id): de gekozen reeks.

    Geeft `project, peilbuis, meetlocatie_id, meetreeks_id, reeks_nr, keuze, kandidaten`;
    `meetreeks_id` en `reeks_nr` zijn leeg als er niets te kiezen is, en `keuze`
    zegt dan waarom.
    """
    per_locatie = {
        mid: groep
        for mid, groep in reeksen.dropna(subset=["meetreeks_id"])
        .drop_duplicates(["meetlocatie_id", "meetreeks_id"])
        .groupby("meetlocatie_id")
    }
    rijen: list[dict] = []
    for rij in config.drop_duplicates(["project", "peilbuis"]).itertuples():
        uit = {"project": rij.project, "peilbuis": rij.peilbuis, "meetlocatie_id": rij.meetlocatie_id,
               "meetreeks_id": None, "reeks_nr": None, "keuze": None, "kandidaten": 0}
        if pd.isna(rij.meetlocatie_id) or not str(rij.meetlocatie_id).strip():
            uit["keuze"] = GEEN_ID
        elif rij.meetlocatie_id not in per_locatie:
            uit["keuze"] = GEEN_REEKS
        else:
            alle: DataFrame = per_locatie[rij.meetlocatie_id]
            bruikbaar = alle[(alle["eenheid"] == EENHEID) & (alle["metingen"].fillna(0) > 0)]
            uit["kandidaten"] = len(bruikbaar)
            vast = getattr(rij, "meetreeks_id", None)
            if pd.notna(vast) and str(vast).strip():
                gekozen = bruikbaar[bruikbaar["meetreeks_id"] == str(vast).strip()]
                uit["keuze"] = VASTGEZET if not gekozen.empty else VASTGEZET_ONBEKEND
            elif bruikbaar.empty:
                gekozen, uit["keuze"] = bruikbaar, GEEN_NAP
            else:
                gekozen = bruikbaar.sort_values(["metingen", "meetreeks_id"], ascending=[False, True]).head(1)
                uit["keuze"] = ENIGE if len(bruikbaar) == 1 else MEESTE
            if not gekozen.empty:
                uit["meetreeks_id"] = gekozen["meetreeks_id"].iloc[0]
                uit["reeks_nr"] = int(gekozen["reeks_nr"].iloc[0])
        rijen.append(uit)
    return DataFrame(rijen)


def export_reeksen(project: str):
    """
    Een `LaadReeksen` voor één project uit de export: per peilbuisnaam het
    daggemiddelde van de gekozen reeks. Een peilbuis zonder reeks ontbreekt.
    """
    config: DataFrame = workbook_lader.lees_config()
    config = config[config["project"] == project]
    keuze: DataFrame = kies_reeksen(config, lees_reeksen()).dropna(subset=["reeks_nr"])
    nrs: dict[int, str] = dict(zip(keuze["reeks_nr"].astype(int), keuze["peilbuis"]))

    dag: DataFrame = pd.read_csv(
        export_map() / DAG_CSV, usecols=["reeks_nr", "datum", "gem"], parse_dates=["datum"]
    )
    dag = dag[dag["reeks_nr"].isin(nrs)]
    per_peilbuis: dict[str, Series] = {
        nrs[int(nr)]: groep.set_index("datum")["gem"].sort_index().rename(nrs[int(nr)])
        for nr, groep in dag.groupby("reeks_nr")
    }

    def laad(namen: list[str]) -> DataFrame:
        kolommen = [per_peilbuis[n] for n in namen if n in per_peilbuis]
        df = pd.concat(kolommen, axis=1) if kolommen else DataFrame(index=pd.DatetimeIndex([]))
        df.index.name = TIJD_KOLOM
        return df.reindex(columns=namen)

    return laad
