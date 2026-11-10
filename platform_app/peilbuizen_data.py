"""
Data for the Peilbuizen map, without Streamlit (so it can be tested).

The wells are every series Databricks has (data_validation's object_data.csv, ~670 series),
not only the ones in a cross section. Positions come from hl_cs_objecten (rd_x/rd_y); a
series that is not linked there has no position and is found by search instead.

Heads: daily values of the series, live from the SQL warehouse when one is set, otherwise
from the hourly export in data_validation/input_output/output_databricks/time_series_data/.
When the series has a review store, its approved hours (with the correction applied) give
the validated line.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
from pandas import DataFrame

DAG_KOLOMMEN = ["datum", "n", "gem", "min", "max"]


def locatie_sleutel(meta: DataFrame) -> pd.Series:
    """One key per physical location: filters of one well share rd_x/rd_y."""
    return meta["rd_x"].round(1).astype(str) + "," + meta["rd_y"].round(1).astype(str)


def label(rij) -> str:
    """`HB28-PB1 · Dijkvak 12 · fugro` for the search box and titles."""
    naam = getattr(rij, "cs_naam", None)
    delen = [str(rij.code), str(naam) if isinstance(naam, str) and naam else "", str(rij.vendor)]
    return " · ".join(d for d in delen if d)


def locaties(meta: DataFrame) -> DataFrame:
    """One row per location with a position: lon, lat, the series there and a hover text."""
    op = meta[meta["has_location"]].assign(loc=lambda d: locatie_sleutel(d))
    rijen = []
    for sleutel, g in op.groupby("loc", sort=False):
        g = g.sort_values("eind_filter", ascending=False, na_position="last")
        eerste = g.iloc[0]
        kop = eerste["cs_naam"] if isinstance(eerste["cs_naam"], str) else eerste["code"]
        regels = [f"<b>{kop}</b>", f"RD {eerste['rd_x']:.0f}, {eerste['rd_y']:.0f}"]
        regels += [f"{r.code} · filter tot {r.eind_filter:.2f} m NAP" if pd.notna(r.eind_filter) else str(r.code)
                   for r in g.itertuples()]
        rijen.append({"loc": sleutel, "lon": eerste["map_lon"], "lat": eerste["map_lat"],
                      "vendor": eerste["vendor"], "n_reeksen": len(g), "hover": "<br>".join(regels)})
    return DataFrame(rijen, columns=["loc", "lon", "lat", "vendor", "n_reeksen", "hover"])


def reeksen_op_locatie(meta: DataFrame, sleutel: str) -> DataFrame:
    """The series at one location, shallowest filter first."""
    op = meta[meta["has_location"]]
    return op[locatie_sleutel(op) == sleutel].sort_values("eind_filter", ascending=False, na_position="last")


def dag_uit_export(pad: Path) -> DataFrame:
    """Daily n/mean/min/max of the hourly export CSV (`Time,head`); empty if there is none."""
    if not Path(pad).is_file():
        return DataFrame(columns=DAG_KOLOMMEN)
    uur = pd.read_csv(pad, parse_dates=["Time"]).dropna(subset=["head"])
    if uur.empty:
        return DataFrame(columns=DAG_KOLOMMEN)
    dag = uur.groupby(uur["Time"].dt.floor("D"))["head"].agg(["size", "mean", "min", "max"])
    dag = dag.rename(columns={"size": "n", "mean": "gem"}).rename_axis("datum").reset_index()
    return dag.round({"gem": 4, "min": 4, "max": 4})


def dag_gevalideerd(store: DataFrame | None) -> DataFrame:
    """Daily mean of the approved hours (gecontroleerd and not afgekeurd), corrected."""
    if store is None or store.empty:
        return DataFrame(columns=["datum", "gem", "n"])
    ok = store[(store["gecontroleerd"] == 1) & (store["afgekeurd"] != 1)].dropna(subset=["raw_data"])
    if ok.empty:
        return DataFrame(columns=["datum", "gem", "n"])
    waarde = ok["raw_data"] + ok.get("correctie", 0.0)
    dag = waarde.groupby(ok["timestamp"].dt.floor("D")).agg(["mean", "size"])
    return dag.rename(columns={"mean": "gem", "size": "n"}).rename_axis("datum").reset_index().round({"gem": 4})
