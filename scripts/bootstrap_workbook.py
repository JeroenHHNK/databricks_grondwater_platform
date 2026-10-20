# bootstrap_workbook.py
"""
Eenmalige migratie: bouwt het geconsolideerde werkboek per project.

Leest de oude invoer uit `input_data/_bron/<project>/` (config.yaml,
config_profielen*.xlsx en het metadata-werkboek) en schrijft
`input_data/<project>/<project>.xlsx`. Daarna onderhoudt een mens dat werkboek;
dit script is bedoeld om één keer te draaien, niet als onderdeel van de pipeline.

De winst zit niet in het bestandsformaat maar in het rapport dat eronder staat:
de drie parallelle lijsten in het blad `profielen` worden hier uitgeklapt naar
één rij per peilbuis, en elke plek waar die lijsten niet op elkaar passen komt
bovendrijven.

    uv run python scripts/bootstrap_workbook.py --project Beemster
    uv run python scripts/bootstrap_workbook.py --alle --dry-run
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd
import yaml
from pandas import DataFrame

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from Modules import paths  # noqa: E402
from Modules.analyse import classificeer_peilbuis_zone  # noqa: E402

SCHEMA_VERSIE = 1

# Zone-tokens die `analyse.classificeer_peilbuis_zone` nog niet kent en die daar
# dus vandaag None opleveren. 'KR' staat in Beemster, Purmer en Schermer tussen
# BUKR (pos 1) en BIB/BIKR in, dus op de kruin. Hier expliciet aangevuld; de
# classifier in analyse.py blijft ongemoeid, want die raakt de freatische lijn.
EXTRA_ZONE_TOKENS: dict[str, str] = {"KR": "kruin"}
_EXTRA_TOKEN_REGEX = re.compile(r"(?:^|_)(KR)(?:_|$)")


@dataclass
class Rapport:
    """Alles wat tijdens de migratie opvalt, per project."""

    project: str
    n_profielen: int = 0
    n_peilbuizen: int = 0
    lengte_conflicten: list[str] = field(default_factory=list)
    zone_onbekend: list[str] = field(default_factory=list)
    zone_aangevuld: list[str] = field(default_factory=list)
    stix_ontbreekt: list[str] = field(default_factory=list)
    blad_ontbreekt: list[str] = field(default_factory=list)
    blad_meerdere: list[str] = field(default_factory=list)
    index_buiten_bereik: list[str] = field(default_factory=list)

    @property
    def n_problemen(self) -> int:
        return sum(
            len(x)
            for x in (
                self.lengte_conflicten,
                self.zone_onbekend,
                self.stix_ontbreekt,
                self.blad_ontbreekt,
                self.blad_meerdere,
                self.index_buiten_bereik,
            )
        )

    def druk_af(self) -> None:
        kop = f"{self.project}: {self.n_profielen} profielen, {self.n_peilbuizen} peilbuizen"
        print(f"\n{kop}\n{'-' * len(kop)}")
        secties = [
            ("lijstlengtes verschillen", self.lengte_conflicten),
            ("zone niet af te leiden uit de code", self.zone_onbekend),
            ("zone aangevuld (analyse.py kent dit token niet)", self.zone_aangevuld),
            ("geen stix-bestand gevonden", self.stix_ontbreekt),
            ("geen blad in de meetdata", self.blad_ontbreekt),
            ("meerdere bladen in de meetdata", self.blad_meerdere),
            ("metadata-index buiten bereik", self.index_buiten_bereik),
        ]
        gemeld = False
        for titel, regels in secties:
            if regels:
                gemeld = True
                print(f"  {titel} ({len(regels)}):")
                for regel in regels:
                    print(f"    - {regel}")
        if not gemeld:
            print("  geen bijzonderheden")


# --- parsers op de oude, string-gebaseerde kolommen -------------------------


def _splits_komma(waarde: Any) -> list[str]:
    if waarde is None or pd.isna(waarde):
        return []
    return [deel.strip() for deel in str(waarde).split(",") if deel.strip()]


def _splits_x_coordinaten(waarde: Any) -> list[float]:
    """'32,21/36/52,94' -> [32.21, 36.0, 52.94]. Punt-decimalen komen ook voor."""
    if waarde is None or pd.isna(waarde):
        return []
    resultaat: list[float] = []
    for deel in str(waarde).split("/"):
        deel = deel.strip().replace(",", ".")
        if deel:
            resultaat.append(float(deel))
    return resultaat


def _splits_indices(waarde: Any) -> list[int]:
    """'50,52,53' -> [50, 52, 53]. Excel maakt van '51,54' soms '51.54'."""
    if waarde is None or pd.isna(waarde):
        return []
    return [int(deel) for deel in re.split(r"[,.]", str(waarde)) if deel.strip()]


def _naar_profiel_id(naam: str) -> str:
    """'Profiel 1' -> 'P01', 'Profiel 12A' -> 'P12A'."""
    match = re.fullmatch(r"\s*Profiel\s+(\d+)\s*([AB]?)\s*", str(naam))
    if not match:
        raise ValueError(f"Onverwachte profielnaam: {naam!r}")
    nummer, suffix = match.groups()
    return f"P{int(nummer):02d}{suffix}"


def _getal(waarde: Any) -> float | str:
    """Excel-cel naar float; lege cellen blijven leeg zodat het werkboek leesbaar blijft."""
    if waarde is None or pd.isna(waarde):
        return ""
    return float(str(waarde).strip().replace(",", "."))


def _tekst(waarde: Any) -> str:
    """Excel-cel naar tekst. Let op: interne spaties blijven staan, die zijn data."""
    if waarde is None or (not isinstance(waarde, str) and pd.isna(waarde)):
        return ""
    return str(waarde).strip()


# --- opzoeken van stix-bestanden en meetdatabladen -------------------------


def _stix_per_profiel(project: str) -> dict[str, Path]:
    """Mapt 'Profiel 1' op het stix-bestand met DWP001 in de naam."""
    map_pad: Path = paths.stix_map(project)
    if not map_pad.is_dir():
        return {}
    gevonden: dict[str, Path] = {}
    for bestand in sorted(map_pad.glob("*.stix")):
        match = re.search(r"DWP(\d+)", bestand.stem)
        if match:
            gevonden[f"Profiel {int(match.group(1))}"] = bestand
    return gevonden


def _bladen_meetdata(project: str, bestandsnaam: str | None) -> list[str]:
    if not bestandsnaam:
        return []
    try:
        pad: Path = paths.zoek_invoerbestand(project, bestandsnaam)
    except FileNotFoundError:
        return []
    return list(pd.ExcelFile(pad).sheet_names)


def _zoek_blad(naam: str, bladen: list[str]) -> tuple[str, int]:
    """
    Zoekt het blad dat bij een peilbuis hoort.

    De oude loader nam de eerste treffer van `naam in blad`; met namen als
    HB001PB01 en HB001PB01x in bladnamen die Excel op 31 tekens afkapt, kan dat
    stil het verkeerde blad pakken. Hier geven we het aantal treffers terug zodat
    het rapport erover kan klagen.
    """
    treffers: list[str] = [blad for blad in bladen if naam in blad]
    if not treffers:
        return "", 0
    return treffers[0], len(treffers)


# --- de migratie zelf ------------------------------------------------------


def _lees_oude_invoer(project: str) -> tuple[dict, DataFrame, DataFrame, Path]:
    bron: Path = paths.bron_map(project)
    config: dict = yaml.safe_load((bron / "config.yaml").read_text(encoding="utf-8"))
    werkboek: Path = bron / config["config_file"]
    bladen: list[str] = config["config_sheets"]

    algemeen: DataFrame = pd.read_excel(werkboek, sheet_name=bladen[0], index_col="key")
    profielen: DataFrame = pd.read_excel(werkboek, sheet_name=bladen[1], dtype=str)
    metadata: DataFrame = pd.read_excel(bron / config["metadata_file"])

    config["_algemeen"] = algemeen["value"].to_dict()
    return config, profielen, metadata, werkboek


def _bouw_project_blad(project: str, config: dict, rapport: Rapport) -> DataFrame:
    algemeen: dict = config["_algemeen"]
    meetdata: str = config["peilbuisdata_file"]
    # De meetexports zijn bij de verhuizing per project geprefixt.
    if meetdata.startswith("peilbuisdata_alle_sensoren_"):
        meetdata = (
            f"peilbuisdata_{project}_alle_sensoren_"
            f"{meetdata[len('peilbuisdata_alle_sensoren_'):]}"
        )

    stix: dict[str, Path] = _stix_per_profiel(project)
    projectnummer: str = ""
    if stix:
        eerste: str = next(iter(stix.values())).stem
        projectnummer = eerste.split("_")[0]

    rijen: list[tuple[str, Any]] = [
        ("schema_versie", SCHEMA_VERSIE),
        ("project_naam", project),
        ("projectnummer", projectnummer),
        ("buitenwaterstand", algemeen.get("buitenwaterstand", "")),
        ("maatgevend_boezempeil_offset", algemeen.get("maatgevend_boezempeil_offset", "")),
        ("peilbuisdata_bestand", meetdata),
        ("peilbuisdata_header_rij", 2),
        ("neerslagdata_bestand", config.get("neerslagdata_file", "")),
        ("stix_map", paths.STIX_MAP),
        ("terugkeertijden_bestand", config.get("return_periods_file", "") or ""),
        ("buitenwaterstand_bestand", config.get("buitenwaterstand_file", "") or ""),
        ("crs", "EPSG:28992"),
        ("metadata_bron", f"_bron/{project}/{config['metadata_file']}"),
        ("notities", ""),
    ]
    return DataFrame(rijen, columns=["key", "value"])


def _bouw_bladen(
    project: str, config: dict, profielen: DataFrame, metadata: DataFrame, rapport: Rapport
) -> tuple[DataFrame, DataFrame, dict[str, DataFrame]]:
    stix: dict[str, Path] = _stix_per_profiel(project)
    bladen_meetdata: list[str] = _bladen_meetdata(
        project, _bouw_project_blad(project, config, rapport)
        .set_index("key")["value"]
        .get("peilbuisdata_bestand")
    )
    projectnummer_kolom = "Projectnummer" if "Projectnummer" in metadata.columns else None

    profiel_rijen: list[dict[str, Any]] = []
    peilbuis_rijen: list[dict[str, Any]] = []

    for _, rij in profielen.iterrows():
        naam: str = str(rij["naam"]).strip()
        profiel_id: str = _naar_profiel_id(naam)
        niveau: str = profiel_id[-1] if profiel_id[-1] in "AB" else "hoofd"
        hoofd_id: str = profiel_id[:-1] if niveau in "AB" else ""

        stix_bestand: Path | None = stix.get(naam)
        if niveau == "hoofd" and stix_bestand is None:
            rapport.stix_ontbreekt.append(f"{naam} ({profiel_id})")

        profiel_rijen.append(
            {
                "profiel_id": profiel_id,
                "naam": naam,
                "hoofdprofiel_id": hoofd_id,
                "diepte_niveau": niveau,
                "profiel_index": rij.get("profiel_index", ""),
                "x_rd": _getal(rij.get("x")),
                "y_rd": _getal(rij.get("y")),
                "teensloot_peil": _getal(rij.get("teensloot_peil")),
                "stix_bestand": stix_bestand.name if stix_bestand else "",
                "stix_offset_x_handmatig": _getal(rij.get("stix_offset_x_handmatig")),
                "coords_blad": f"coords_{profiel_id}" if not pd.isna(rij.get("coords_sheet")) else "",
                "actief": True,
                "opmerkingen": rij.get("Opmerkingen", "") if not pd.isna(rij.get("Opmerkingen")) else "",
            }
        )

        namen: list[str] = _splits_komma(rij.get("stijghoogte_namen"))
        codes: list[str] = _splits_komma(rij.get("Code_peilbuis"))
        x_coords: list[float] = _splits_x_coordinaten(rij.get("x_coordinaten_dwarsprofiel"))
        indices: list[int] = _splits_indices(rij.get("metadata_pb_indices"))

        lengtes: dict[str, int] = {
            "stijghoogte_namen": len(namen),
            "Code_peilbuis": len(codes),
            "x_coordinaten": len(x_coords),
            "metadata_indices": len(indices),
        }
        if len({v for v in lengtes.values() if v}) > 1:
            rapport.lengte_conflicten.append(f"{naam}: {lengtes}")

        for positie, peilbuis_naam in enumerate(namen, start=1):
            index: int | None = indices[positie - 1] if positie <= len(indices) else None
            md_rij: pd.Series | None = None
            if index is not None:
                if index in metadata.index:
                    md_rij = metadata.loc[index]
                else:
                    rapport.index_buiten_bereik.append(f"{naam} pos {positie}: index {index}")

            # De pipeline leest Code_peilbuis uit het metadata-werkboek, niet uit
            # het blad `profielen`. Die twee lopen uiteen (13_6 heeft in de
            # metadata 'PB511-2 _F-752' met spatie, in profielen zonder), dus de
            # metadata is hier leidend.
            # Niet strippen: een spatie in de code is data. 13_6 heeft codes als
            # 'PB483-1 _F-207', en de peilbuiscodes op het platform
            # (`meetreeks.code`) hebben dezelfde vorm.
            code: str = ""
            if md_rij is not None and "Code_peilbuis" in metadata.columns:
                ruw = md_rij["Code_peilbuis"]
                code = "" if ruw is None or pd.isna(ruw) else str(ruw)
            if not code and positie <= len(codes):
                code = codes[positie - 1]

            zone: str = classificeer_peilbuis_zone(code) or ""
            if not zone:
                match = _EXTRA_TOKEN_REGEX.search(code or "")
                if match:
                    zone = EXTRA_ZONE_TOKENS[match.group(1)]
                    rapport.zone_aangevuld.append(
                        f"{naam} pos {positie} ({peilbuis_naam}): token "
                        f"'{match.group(1)}' -> '{zone}'"
                    )
            if not zone:
                # Niet raden: een verkeerde zone verschuift de freatische lijn.
                # Leeg laten en melden, zodat een mens het invult.
                rapport.zone_onbekend.append(
                    f"{naam} pos {positie} ({peilbuis_naam}, code {code or 'leeg'}) -> handmatig invullen"
                )

            blad, n_treffers = _zoek_blad(peilbuis_naam, bladen_meetdata)
            if bladen_meetdata and n_treffers == 0:
                rapport.blad_ontbreekt.append(f"{naam} pos {positie}: {peilbuis_naam}")
            elif n_treffers > 1:
                rapport.blad_meerdere.append(
                    f"{naam} pos {positie}: {peilbuis_naam} -> {n_treffers} bladen, gekozen {blad!r}"
                )

            def md(kolom: str) -> Any:
                if md_rij is None or kolom not in metadata.columns:
                    return ""
                return _getal(md_rij[kolom]) if kolom != projectnummer_kolom else md_rij[kolom]

            peilbuis_rijen.append(
                {
                    "profiel_id": profiel_id,
                    "positie": positie,
                    "zone": zone,
                    "peilbuis_naam": peilbuis_naam,
                    "code_peilbuis": code,
                    "projectnummer": (
                        md_rij[projectnummer_kolom]
                        if md_rij is not None and projectnummer_kolom
                        else ""
                    ),
                    "peilbuisdata_blad": blad,
                    "x_dwarsprofiel": x_coords[positie - 1] if positie <= len(x_coords) else "",
                    "hoogte_maaiveld_nap": md("Hoogte_maaiveld_NAP"),
                    "hoogte_bovenkant_peilbuis": md("Hoogte_bovenkant_peilbuis"),
                    "bovenkant_filter_nap": md("bovenkant_filter_M_NAP"),
                    "onderkant_filter_nap": md("onderkant_filter_M_NAP"),
                    "lengte_stijgbuisdeel_m": md("lengte_stijgbuisdeel_M"),
                    "filterlengte_m": md("filterlengte_M"),
                    "x_rd": md("x"),
                    "y_rd": md("y"),
                    "in_boxplot": niveau == "hoofd",
                    # Worden door notebooks/40 voorgesteld en door een mens bevestigd.
                    "meetreeks_id": "",
                    "meetreeks_code": "",
                    "match_methode": "",
                    "match_score": "",
                    "match_afstand_m": "",
                    "match_datum": "",
                    "handmatig_vastgezet": False,
                    "opmerking": "",
                }
            )

    rapport.n_profielen = len(profiel_rijen)
    rapport.n_peilbuizen = len(peilbuis_rijen)

    # Dwarsprofielgeometrie: coords_profiel_<n> -> coords_<profiel_id>
    bron: Path = paths.bron_map(project)
    werkboek: Path = bron / config["config_file"]
    beschikbaar: list[str] = list(pd.ExcelFile(werkboek).sheet_names)
    coords: dict[str, DataFrame] = {}
    for profiel in profiel_rijen:
        oud_blad = profielen.loc[
            profielen["naam"].astype(str).str.strip() == profiel["naam"], "coords_sheet"
        ]
        oud: Any = oud_blad.iloc[0] if len(oud_blad) else None
        if oud is None or pd.isna(oud) or str(oud) not in beschikbaar:
            continue
        coords[f"coords_{profiel['profiel_id']}"] = pd.read_excel(
            werkboek, sheet_name=str(oud)
        )[["x", "y"]]

    return DataFrame(profiel_rijen), DataFrame(peilbuis_rijen), coords


def bouw_workbook(project: str, *, dry_run: bool = False) -> Rapport:
    rapport = Rapport(project=project)
    config, profielen, metadata, _ = _lees_oude_invoer(project)

    blad_project: DataFrame = _bouw_project_blad(project, config, rapport)
    blad_profielen, blad_peilbuizen, coords = _bouw_bladen(
        project, config, profielen, metadata, rapport
    )

    if not dry_run:
        doel: Path = paths.workbook_pad(project)
        with pd.ExcelWriter(doel, engine="openpyxl") as writer:
            blad_project.to_excel(writer, sheet_name="project", index=False)
            blad_profielen.to_excel(writer, sheet_name="profielen", index=False)
            blad_peilbuizen.to_excel(writer, sheet_name="peilbuizen", index=False)
            for naam, df in coords.items():
                df.to_excel(writer, sheet_name=naam, index=False)
        print(f"  geschreven: {doel.relative_to(paths.repo_root())}")

    return rapport


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    groep = parser.add_mutually_exclusive_group(required=True)
    groep.add_argument("--project", help="projectnaam, bv. Beemster")
    groep.add_argument("--alle", action="store_true", help="alle projecten")
    parser.add_argument(
        "--dry-run", action="store_true", help="alleen rapporteren, niets schrijven"
    )
    args = parser.parse_args()

    projecten: list[str] = paths.projecten() if args.alle else [args.project]
    rapporten: list[Rapport] = []
    for project in projecten:
        print(f"\n=== {project} ===")
        rapporten.append(bouw_workbook(project, dry_run=args.dry_run))

    print(f"\n{'=' * 70}\nRAPPORT")
    for rapport in rapporten:
        rapport.druk_af()

    totaal: int = sum(r.n_problemen for r in rapporten)
    print(f"\n{'=' * 70}\n{totaal} punt(en) om na te lopen in de werkboeken.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
