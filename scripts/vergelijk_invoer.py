# vergelijk_invoer.py
"""
Bewijst dat het nieuwe werkboek dezelfde invoer oplevert als de oude bestanden.

Vergelijkt, per project, wat `workbook_lader` uit `input_data/<p>/<p>.xlsx` haalt
met wat `config_lader` + `data_lader` uit `input_data/_bron/<p>/` halen. Zolang de
invoer aantoonbaar gelijk is, kan de uitvoer niet veranderen -- dat is een
goedkopere en hardere controle dan HTML-plots naast elkaar leggen.

    uv run python scripts/vergelijk_invoer.py --alle
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd
import yaml
from pandas import DataFrame

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from Modules import config_lader, paths, workbook_lader  # noqa: E402
from Modules.data_lader import bereid_metadata_peilbuis_voor  # noqa: E402

METADATA_KOLOMMEN: list[str] = [
    "Code_peilbuis",
    "Hoogte_bovenkant_peilbuis",
    "bovenkant_filter_M_NAP",
    "onderkant_filter_M_NAP",
]


def _oude_invoer(project: str) -> tuple[dict, DataFrame, DataFrame]:
    bron: Path = paths.bron_map(project)
    config: dict = yaml.safe_load((bron / "config.yaml").read_text(encoding="utf-8"))
    algemeen, profielen = config_lader.laad_excel_config(
        bron, config["config_file"], config["config_sheets"]
    )
    metadata: DataFrame = pd.read_excel(bron / config["metadata_file"])
    return algemeen, profielen, metadata


def _vergelijk_getal(oud, nieuw, label: str, fouten: list[str]) -> None:
    oud_leeg = oud is None or (isinstance(oud, float) and pd.isna(oud)) or oud == ""
    nieuw_leeg = nieuw is None or (isinstance(nieuw, float) and pd.isna(nieuw)) or nieuw == ""
    if oud_leeg and nieuw_leeg:
        return
    if oud_leeg != nieuw_leeg:
        fouten.append(f"{label}: oud={oud!r} nieuw={nieuw!r} (een van beide leeg)")
        return
    if abs(float(oud) - float(nieuw)) > 1e-9:
        fouten.append(f"{label}: oud={oud!r} nieuw={nieuw!r}")


def vergelijk(project: str) -> list[str]:
    fouten: list[str] = []
    oud_algemeen, oud_profielen, oud_metadata = _oude_invoer(project)
    invoer = workbook_lader.laad_project(project)

    # 1. algemene config
    for sleutel in ("buitenwaterstand", "maatgevend_boezempeil_offset"):
        if sleutel in oud_algemeen:
            _vergelijk_getal(
                oud_algemeen[sleutel],
                invoer.algemene_config.get(sleutel),
                f"algemene_config[{sleutel}]",
                fouten,
            )

    # 2. profielen: rij-voor-rij, op naam
    oud_namen: list[str] = [str(n).strip() for n in oud_profielen["naam"]]
    nieuw_namen: list[str] = [str(n).strip() for n in invoer.profielen_df["naam"]]
    if oud_namen != nieuw_namen:
        fouten.append(f"profielnamen/volgorde wijkt af:\n  oud={oud_namen}\n  nieuw={nieuw_namen}")
        return fouten

    for (_, oud_rij), (_, nieuw_rij) in zip(
        oud_profielen.iterrows(), invoer.profielen_df.iterrows()
    ):
        naam: str = str(oud_rij["naam"]).strip()

        # De parallelle lijsten, teruggebouwd uit de lange tabel.
        # `Code_peilbuis` wordt hier NIET vergeleken: de pipeline leest die kolom
        # alleen uit het metadata-werkboek (via bereid_metadata_peilbuis_voor),
        # nooit uit het blad `profielen`. Die twee lopen in 13_6 uiteen, en de
        # metadata is leidend. Punt 3 hieronder controleert de echte waarden.
        for kolom in ("stijghoogte_namen",):
            oud_lijst = [
                d.strip() for d in str(oud_rij.get(kolom) or "").split(",") if d.strip()
            ]
            nieuw_lijst = [
                d.strip() for d in str(nieuw_rij.get(kolom) or "").split(",") if d.strip()
            ]
            if oud_lijst != nieuw_lijst:
                fouten.append(f"{naam}.{kolom}: oud={oud_lijst} nieuw={nieuw_lijst}")

        oud_x = [
            float(d.strip().replace(",", "."))
            for d in str(oud_rij.get("x_coordinaten_dwarsprofiel") or "").split("/")
            if d.strip()
        ]
        nieuw_x = [
            float(d.strip().replace(",", "."))
            for d in str(nieuw_rij.get("x_coordinaten_dwarsprofiel") or "").split("/")
            if d.strip()
        ]
        if len(oud_x) != len(nieuw_x) or any(
            abs(a - b) > 1e-9 for a, b in zip(oud_x, nieuw_x)
        ):
            fouten.append(f"{naam}.x_coordinaten: oud={oud_x} nieuw={nieuw_x}")

        for kolom in ("x", "y", "teensloot_peil", "stix_offset_x_handmatig"):
            _vergelijk_getal(
                oud_rij.get(kolom), nieuw_rij.get(kolom), f"{naam}.{kolom}", fouten
            )

        # 3. metadata per profiel: wat bereid_metadata_peilbuis_voor zou opleveren
        oud_indices = [
            int(d)
            for d in str(oud_rij.get("metadata_pb_indices") or "")
            .replace(".", ",")
            .split(",")
            if d.strip()
        ]
        if oud_indices:
            oud_md: DataFrame = bereid_metadata_peilbuis_voor(oud_metadata, oud_indices)
            nieuw_md: DataFrame = workbook_lader.metadata_voor_profiel(
                invoer, str(nieuw_rij["profiel_id"])
            )
            if len(oud_md) != len(nieuw_md):
                fouten.append(f"{naam}.metadata: {len(oud_md)} vs {len(nieuw_md)} rijen")
            else:
                for kolom in METADATA_KOLOMMEN:
                    if kolom not in oud_md.columns:
                        continue
                    for i in range(len(oud_md)):
                        a, b = oud_md.iloc[i][kolom], nieuw_md.iloc[i][kolom]
                        if kolom == "Code_peilbuis":
                            if str(a) != str(b):
                                fouten.append(f"{naam}.metadata[{i}].{kolom}: {a!r} vs {b!r}")
                        else:
                            _vergelijk_getal(a, b, f"{naam}.metadata[{i}].{kolom}", fouten)

        # 4. dwarsprofielgeometrie
        if not pd.isna(oud_rij.get("coords_sheet")):
            bron: Path = paths.bron_map(project)
            config: dict = yaml.safe_load(
                (bron / "config.yaml").read_text(encoding="utf-8")
            )
            # Beide kanten dezelfde opschoning: `laad_profiel_data` dwingt x/y naar
            # numeriek en gooit NaN-rijen weg (AutoCAD-plakwerk met vaste spaties).
            oud_coords: DataFrame = workbook_lader._schoon_coords(
                pd.read_excel(
                    bron / config["config_file"], sheet_name=str(oud_rij["coords_sheet"])
                )
            )
            nieuw_coords: DataFrame = workbook_lader.laad_coords(
                invoer, str(nieuw_rij["profiel_id"])
            )
            if len(oud_coords) != len(nieuw_coords):
                fouten.append(
                    f"{naam}.coords: {len(oud_coords)} vs {len(nieuw_coords)} punten"
                )
            else:
                # Met tolerantie: de Excel-heen-en-weer levert ruis op de laatste
                # bit (~1e-14), wat DataFrame.equals() al als verschil ziet.
                verschil = (
                    oud_coords.reset_index(drop=True) - nieuw_coords.reset_index(drop=True)
                ).abs().max().max()
                if verschil > 1e-9:
                    fouten.append(f"{naam}.coords: max verschil {verschil:.3g}")

    return fouten


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    groep = parser.add_mutually_exclusive_group(required=True)
    groep.add_argument("--project")
    groep.add_argument("--alle", action="store_true")
    args = parser.parse_args()

    projecten: list[str] = paths.projecten() if args.alle else [args.project]
    totaal: int = 0
    for project in projecten:
        fouten: list[str] = vergelijk(project)
        totaal += len(fouten)
        vlag: str = "OK " if not fouten else "AF "
        print(f"{vlag} {project}: {len(fouten)} verschil(len)")
        for fout in fouten[:20]:
            print(f"      {fout}")
        if len(fouten) > 20:
            print(f"      ... en {len(fouten) - 20} meer")

    print(f"\n{totaal} verschil(len) in totaal.")
    return 1 if totaal else 0


if __name__ == "__main__":
    raise SystemExit(main())
