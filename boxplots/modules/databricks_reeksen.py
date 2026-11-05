# databricks_reeksen.py
"""
Stijghoogtes uit Databricks, per peilbuis één meetreeks.

Een `DD_meetlocatie_id` in `config.csv` kan meer dan één meetreeks hebben. Dat
zijn geen opvolgende loggers maar parallelle sensoren met vaak heel andere
niveaus (Purmer HB012PB01: gemiddeld -2,23, -0,00 en -5,36 m NAP), dus
samenvoegen kan niet. De keuze, in deze volgorde:

1. alleen reeksen in `m NAP` met minstens één meting;
2. staat er een `DD_meetreeks_id` in `config.csv`, dan die (vastgezet);
3. anders de reeks met de meeste metingen.

Voor de MVP zijn het de ruwe metingen: er wordt niet op `validatie_status`
gefilterd. De waarden zijn daggemiddelden (UTC-dag).

Twee bronnen met dezelfde vorm (`Bron`):
- live: het SQL warehouse, op het moment van vragen (`databricks_live.py`);
- export: de csv's die `notebooks/db_notebook_exporteer_stijghoogtes.py` in
  `input_data/databricks_export/` schrijft. Dat is de terugval als live niet
  is ingesteld of mislukt.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd
from pandas import DataFrame, Series

from . import databricks_live, paths, workbook_lader
from .data_lader import TIJD_KOLOM

EENHEID: str = "m NAP"
REEKSEN_CSV: str = "reeksen.csv"
DAG_CSV: str = "stijghoogtes_dag.csv"

LIVE = "live"
EXPORT = "export"

# Waarom een peilbuis wel of geen reeks krijgt; ook gebruikt door notebooks/controleer_reeksen.py.
VASTGEZET = "vastgezet"
ENIGE = "enige reeks"
MEESTE = "meeste metingen"
GEEN_ID = "geen meetlocatie_id"
GEEN_REEKS = "geen meetreeks op deze meetlocatie"
GEEN_NAP = f"geen reeks in {EENHEID} met metingen"
VASTGEZET_ONBEKEND = "vastgezette meetreeks_id hoort niet bij deze meetlocatie"


@dataclass(frozen=True)
class Bron:
    """De meetreeksen van onze meetlocaties en hun daggemiddelden."""

    soort: str  # LIVE of EXPORT
    # Per meetlocatie per meetreeks: meetlocatie_id, meetreeks_id, meetreeks_code,
    # eenheid, metingen, van, tot. De export heeft ook `reeks_nr` en filters.
    reeksen: DataFrame
    dag: DataFrame  # meetreeks_id, datum, gem
    fout: str | None = None  # waarom live niet lukte, als dit de terugval is

    def laatste_meting(self) -> str:
        return f"{self.dag['datum'].max():%Y-%m-%d}" if not self.dag.empty else "-"


def export_map() -> Path:
    return paths.export_map()


def export_beschikbaar() -> bool:
    return (export_map() / REEKSEN_CSV).is_file() and (export_map() / DAG_CSV).is_file()


def lees_reeksen() -> DataFrame:
    """`reeksen.csv`: per peilbuis per meetreeks, met `reeks_nr`, eenheid en aantal metingen."""
    return pd.read_csv(export_map() / REEKSEN_CSV, dtype={"meetreeks_id": str, "meetlocatie_id": str})


def export_bron() -> Bron:
    reeksen: DataFrame = lees_reeksen()
    dag: DataFrame = pd.read_csv(
        export_map() / DAG_CSV, usecols=["reeks_nr", "datum", "gem"], parse_dates=["datum"]
    )
    # De export gebruikt een kort reeks_nr in plaats van de 64 tekens lange id.
    ids: Series = reeksen.dropna(subset=["reeks_nr"]).drop_duplicates("reeks_nr").set_index("reeks_nr")["meetreeks_id"]
    dag.insert(0, "meetreeks_id", dag.pop("reeks_nr").map(ids))
    return Bron(EXPORT, reeksen, dag)


def live_bron(config: DataFrame | None = None) -> Bron:
    config = workbook_lader.lees_config() if config is None else config
    reeksen, dag = databricks_live.haal_op(config["DD_meetlocatie_id"].dropna().tolist())
    return Bron(LIVE, reeksen, dag)


def bron() -> Bron:
    """Live als er een warehouse is ingesteld; anders, of als dat mislukt, de export."""
    if not databricks_live.beschikbaar():
        return export_bron()
    try:
        return live_bron()
    except Exception as fout:  # noqa: BLE001 - zonder live blijft de export bruikbaar
        if not export_beschikbaar():
            raise
        terugval: Bron = export_bron()
        return Bron(EXPORT, terugval.reeksen, terugval.dag, fout=f"{type(fout).__name__}: {fout}")


def kies_reeksen(config: DataFrame, reeksen: DataFrame) -> DataFrame:
    """
    Per peilbuis in `config` (LD_project, LD_peilbuis, DD_meetlocatie_id,
    DD_meetreeks_id): de gekozen reeks.

    Geeft `project, peilbuis, meetlocatie_id, meetreeks_id, reeks_nr, keuze, kandidaten`;
    `meetreeks_id` is leeg als er niets te kiezen is, en `keuze` zegt dan waarom.
    `reeks_nr` is er alleen bij de export.
    """
    per_locatie = {
        mid: groep
        for mid, groep in reeksen.dropna(subset=["meetreeks_id"])
        .drop_duplicates(["meetlocatie_id", "meetreeks_id"])
        .groupby("meetlocatie_id")
    }
    rijen: list[dict] = []
    for rij in config.drop_duplicates(["LD_project", "LD_peilbuis"]).itertuples():
        mid = rij.DD_meetlocatie_id
        uit = {"project": rij.LD_project, "peilbuis": rij.LD_peilbuis, "meetlocatie_id": mid,
               "meetreeks_id": None, "reeks_nr": None, "keuze": None, "kandidaten": 0}
        if pd.isna(mid) or not str(mid).strip():
            uit["keuze"] = GEEN_ID
        elif mid not in per_locatie:
            uit["keuze"] = GEEN_REEKS
        else:
            alle: DataFrame = per_locatie[mid]
            bruikbaar = alle[(alle["eenheid"] == EENHEID) & (alle["metingen"].fillna(0) > 0)]
            uit["kandidaten"] = len(bruikbaar)
            vast = getattr(rij, "DD_meetreeks_id", None)
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
                if "reeks_nr" in gekozen:
                    uit["reeks_nr"] = int(gekozen["reeks_nr"].iloc[0])
        rijen.append(uit)
    return DataFrame(rijen)


def profielen_met_data(bron: Bron) -> set[tuple[str, str]]:
    """(project, cross_section_key) waarvan minstens één peilbuis van het hoofdprofiel een reeks heeft."""
    config: DataFrame = workbook_lader.lees_config()
    keuze: DataFrame = kies_reeksen(config, bron.reeksen).dropna(subset=["meetreeks_id"])
    hoofd: DataFrame = config[config["LD_diepte_niveau"] == workbook_lader.HOOFD]
    met: DataFrame = hoofd.merge(
        keuze[["project", "peilbuis"]], left_on=["LD_project", "LD_peilbuis"], right_on=["project", "peilbuis"]
    )
    return set(zip(met["LD_project"], met["LD_cross_section_key"]))


def laad_reeksen(bron: Bron, project: str):
    """
    Een `LaadReeksen` voor één project: per peilbuisnaam het daggemiddelde van
    de gekozen reeks. Een peilbuis zonder reeks ontbreekt.
    """
    config: DataFrame = workbook_lader.lees_config()
    config = config[config["LD_project"] == project]
    keuze: DataFrame = kies_reeksen(config, bron.reeksen).dropna(subset=["meetreeks_id"])
    namen: dict[str, str] = dict(zip(keuze["meetreeks_id"], keuze["peilbuis"]))

    dag: DataFrame = bron.dag[bron.dag["meetreeks_id"].isin(namen)]
    per_peilbuis: dict[str, Series] = {
        namen[mid]: groep.set_index("datum")["gem"].sort_index().rename(namen[mid])
        for mid, groep in dag.groupby("meetreeks_id")
    }

    def laad(gevraagd: list[str]) -> DataFrame:
        kolommen = [per_peilbuis[n] for n in gevraagd if n in per_peilbuis]
        df = pd.concat(kolommen, axis=1) if kolommen else DataFrame(index=pd.DatetimeIndex([]))
        df.index.name = TIJD_KOLOM
        return df.reindex(columns=gevraagd)

    return laad
