# zet_export_om.py
"""
Zet een peilbuisdata-export (xlsx, één blad per peilbuis) om naar één csv per peilbuis.

    uv run python scripts/zet_export_om.py --project Beemster
    uv run python scripts/zet_export_om.py --alle

Leest de export waar `peilbuisdata_bestand` in `project.csv` naar wijst, in
`input_data/<project>/peilbuisdata/`, en schrijft `peilbuisdata/<peilbuis_naam>.csv`
voor elke peilbuis uit `peilbuizen.csv`. Bestaande csv's worden overschreven.

De app leest die csv's in plaats van de export, omdat dat veel sneller is.
Zodra de stijghoogtes uit Databricks komen, is dit script niet meer nodig.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from Modules import data_lader, paths, workbook_lader  # noqa: E402


def zet_export_om(project: str) -> None:
    invoer = workbook_lader.laad_project(project)
    export: Path | None = invoer.paden["peilbuisdata"]
    if export is None:
        print(f"⚠️ {project}: geen export '{invoer.algemene_config.get('peilbuisdata_bestand')}' in peilbuisdata/")
        return
    namen: list[str] = list(dict.fromkeys(str(n).strip() for n in invoer.peilbuizen_df["peilbuis_naam"]))
    reeksen = data_lader.lees_export_reeksen(export, namen)
    for naam, reeks in reeksen.items():
        data_lader.schrijf_reeks_csv(reeks, paths.reeks_csv(project, naam))
    ontbrekend: list[str] = [n for n in namen if n not in reeksen]
    print(f"✅ {project}: {len(reeksen)} reeksen uit {export.name}"
          + (f"; geen blad voor {', '.join(ontbrekend)}" if ontbrekend else ""))


def _argumenten() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    groep = parser.add_mutually_exclusive_group(required=True)
    groep.add_argument("--project", action="append", choices=paths.projecten(), metavar="NAAM")
    groep.add_argument("--alle", action="store_true")
    return parser.parse_args()


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    args = _argumenten()
    for naam in paths.projecten() if args.alle else args.project:
        zet_export_om(naam)
