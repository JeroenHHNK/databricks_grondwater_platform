"""
Where the app reads the Pastas results from, without Streamlit (so it can be tested).

Two sources with the same shape, per data basis (`unvalidated` / `validated`, the
`unvalid_pastas_*` / `valid_pastas_*` tables):

- live: the Delta tables, through the SQL warehouse of `DATABRICKS_WAREHOUSE_ID`, as the
  app's service principal. Needs `SELECT` on the tables (Refresh data checks this);
- export: `pastas/results_export/<prefix>/`, written by
  `pastas/notebooks/analysis/export_results.py` and pushed to git.

Live is tried first when a warehouse is set; if that fails (no grant, no table yet) the
export is used and the reason is returned with the data. Either way the result is the
latest run per series and model type (`tarso_pipeline.results.latest_run_sql`).

The family picks are always the git file `pastas/family_choice/<prefix>_family_choice.csv`:
the app writes it (locally) and the Databricks table is loaded from it.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd
from pandas import DataFrame

from modules import databricks_live
from tarso_pipeline import results
from tarso_pipeline.paths import DATA_BASES, TABLE_PREFIX, UNVALIDATED, VALIDATED, output_table

ROOT = Path(__file__).resolve().parents[1]
PASTAS = ROOT / "pastas"
BASIS_LABEL = {UNVALIDATED: "Ongevalideerd (ruwe bron)", VALIDATED: "Gevalideerd (goedgekeurd + correctie)"}
LIVE, EXPORT, GEEN = "live", "export", "geen"


@dataclass(frozen=True)
class Resultaat:
    data: DataFrame
    bron: str          # LIVE, EXPORT or GEEN
    melding: str = ""  # why live was not used, or why there is nothing


def _export(basis: str, kind: str, meetreeks_id: str | None) -> Resultaat:
    pad = results.export_dir(PASTAS, basis) / results.EXPORT_FILES[kind]
    if not pad.is_file():
        return Resultaat(DataFrame(), GEEN, f"geen export {pad.relative_to(ROOT).as_posix()}")
    frame = pd.read_parquet(pad) if pad.suffix == ".parquet" else pd.read_csv(pad)
    if meetreeks_id is not None:
        frame = frame[frame["meetreeks_id"] == meetreeks_id]
    return Resultaat(results.latest_rows(frame), EXPORT)


def lees(basis: str, kind: str, meetreeks_id: str | None = None) -> Resultaat:
    """The latest run of one result table, live when possible, else the export."""
    melding = ""
    if databricks_live.beschikbaar():
        try:
            with databricks_live.verbinding() as cursor:
                cursor.execute(results.latest_run_sql(output_table(basis, kind), meetreeks_id=meetreeks_id))
                return Resultaat(cursor.fetchall_arrow().to_pandas(), LIVE)
        except Exception as fout:  # noqa: BLE001 - fall back to the export
            melding = f"live mislukte ({str(fout).splitlines()[0][:200] if str(fout) else type(fout).__name__})"
    uit = _export(basis, kind, meetreeks_id)
    return Resultaat(uit.data, uit.bron, "; ".join(m for m in (melding, uit.melding) if m))


def keuze_pad(basis: str) -> Path:
    return results.family_choice_path(PASTAS, basis)


def lees_keuze(basis: str) -> DataFrame:
    return results.read_family_choice(keuze_pad(basis))


def prefix(basis: str) -> str:
    return TABLE_PREFIX[basis]


def plot(basis: str, series_name: str, kind: str) -> Path | None:
    """The exported extremes figure of one model (`results.PLOT_KINDS`), or None.

    Always from git (`results_export/<prefix>/plots/`): the figures live on the volume,
    which the app cannot read.
    """
    pad = results.plot_path(PASTAS, basis, series_name, kind)
    return pad if pad.is_file() else None


__all__ = ["BASIS_LABEL", "DATA_BASES", "EXPORT", "GEEN", "LIVE", "Resultaat", "keuze_pad", "lees",
           "lees_keuze", "plot", "prefix"]
