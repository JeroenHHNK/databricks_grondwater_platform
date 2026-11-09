"""
Data for the Stijghoogtes section, without Streamlit (so it can be tested).

The catalogue is boxplots/input_data/config.csv, one row per well per cross section,
with the series that databricks_reeksen.kies_reeksen picks for it (the same choice the
Boxplots plots use) and the dike zone read from the well code.
"""

from __future__ import annotations

import pandas as pd
from pandas import DataFrame

from modules import analyse, databricks_reeksen, workbook_lader

ONBEKEND = "onbekend"

# The zone token in the well code, e.g. B_D11-DP_79+1_BIKR_GMW_PB2_F-4.26, from outside
# (river side) to inside. Order of the legend and of the zone pickers.
ZONE_VOLGORDE: list[str] = [
    "BUTAL", "BUT", "BUKR", "BIKR", "BIRK", "KRBIB", "BIB", "INBIB", "INST", "INSD",
    "BITA", "BIT", ONBEKEND,
]
ZONE_UITLEG: dict[str, str] = {
    "BUKR": "buitenkruin", "BIKR": "binnenkruin", "BIRK": "binnenkruin (BIRK)",
    "KRBIB": "kruin binnenberm", "BIB": "binnenberm", "INBIB": "insteek binnenberm",
    "INST": "insteek", "INSD": "insteek sloot", "BIT": "binnenteen", "BITA": "binnentalud",
    "BUT": "buitenteen", "BUTAL": "buitentalud", ONBEKEND: "geen zone in de code",
}

KOLOMMEN: list[str] = [
    "project", "dwarsprofiel", "diepte_niveau", "peilbuis", "code", "zone", "zonegroep",
    "x_profiel", "bk_peilbuis", "bk_filter", "ok_filter", "meetreeks_id", "keuze",
]


def zone_token(code: object) -> str:
    """The zone token in a well code (BUKR, BIKR, BIT, …), or `onbekend`. INST* counts as INST."""
    match = analyse._ZONE_TOKEN_REGEX.search(str(code)) if isinstance(code, str) else None
    if not match:
        return ONBEKEND
    token = match.group(1)
    return "INST" if token.startswith("INST") else token


def catalogus(bron: databricks_reeksen.Bron, config: DataFrame | None = None) -> DataFrame:
    """One row per well per cross section (KOLOMMEN); `meetreeks_id` is empty without a series."""
    config = workbook_lader.lees_config() if config is None else config
    keuze: DataFrame = databricks_reeksen.kies_reeksen(config, bron.reeksen)
    rijen: DataFrame = config.merge(
        keuze[["project", "peilbuis", "meetreeks_id", "keuze"]],
        left_on=["LD_project", "LD_peilbuis"], right_on=["project", "peilbuis"], how="left",
    )
    uit = DataFrame({
        "project": rijen["LD_project"],
        "dwarsprofiel": rijen["LD_cross_section_key"],
        "diepte_niveau": rijen["LD_diepte_niveau"],
        "peilbuis": rijen["LD_peilbuis"],
        "code": rijen["LD_code"],
        "zone": rijen["LD_code"].map(zone_token),
        "zonegroep": rijen["LD_code"].map(lambda c: analyse.classificeer_peilbuis_zone(c) or ONBEKEND),
        "x_profiel": pd.to_numeric(rijen["LD_x_profiel"], errors="coerce"),
        "bk_peilbuis": pd.to_numeric(rijen["LD_bk_peilbuis"], errors="coerce"),
        "bk_filter": pd.to_numeric(rijen["LD_bk_filter"], errors="coerce"),
        "ok_filter": pd.to_numeric(rijen["LD_ok_filter"], errors="coerce"),
        "meetreeks_id": rijen["meetreeks_id"],
        "keuze": rijen["keuze"],
    })
    return uit.sort_values(["project", "dwarsprofiel", "diepte_niveau", "x_profiel"], ignore_index=True)


def dagwaarden(bron: databricks_reeksen.Bron, meetreeks_ids: list[str]) -> DataFrame:
    """The daily rows (meetreeks_id, datum, gem, and min/max/n_status_1 when the source has them)."""
    dag: DataFrame = bron.dag[bron.dag["meetreeks_id"].isin(meetreeks_ids)]
    return dag.sort_values(["meetreeks_id", "datum"], ignore_index=True)


def samenvatting(cat: DataFrame, dag: DataFrame) -> DataFrame:
    """Per well in `cat`: first and last day, number of days, mean/min/max of the daily means."""
    per = dag.groupby("meetreeks_id").agg(
        van=("datum", "min"), tot=("datum", "max"), dagen=("datum", "nunique"),
        gemiddeld=("gem", "mean"), laagste=("gem", "min"), hoogste=("gem", "max"),
    ).round({"gemiddeld": 3, "laagste": 3, "hoogste": 3})
    return cat.merge(per, left_on="meetreeks_id", right_index=True, how="left")


def label(rij) -> str:
    """`MB044PB02 · BIKR · D11-DP_0079+6 (A) · 13_6` for pickers and legends."""
    niveau = "" if rij.diepte_niveau == workbook_lader.HOOFD else f" ({rij.diepte_niveau})"
    return f"{rij.peilbuis} · {rij.zone} · {rij.dwarsprofiel}{niveau} · {rij.project}"


def zones_in(cat: DataFrame) -> list[str]:
    """The zones present in `cat`, in ZONE_VOLGORDE."""
    aanwezig = set(cat["zone"])
    return [z for z in ZONE_VOLGORDE if z in aanwezig]


def breed(dag: DataFrame, namen: dict[str, str]) -> DataFrame:
    """Daily means side by side, one column per series (named by `namen`), for a CSV download."""
    if dag.empty:
        return DataFrame()
    tabel = dag.pivot_table(index="datum", columns="meetreeks_id", values="gem")
    return tabel.rename(columns=namen).sort_index()
