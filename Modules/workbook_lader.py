# workbook_lader.py
"""
Laadt het geconsolideerde werkboek `input_data/<project>/<project>.xlsx`.

Het blad `peilbuizen` is de bron van waarheid: één rij per peilbuis per profiel.
Voor de bestaande pipeline bouwt deze module daaruit de oude, string-gebaseerde
kolommen terug (`stijghoogte_namen`, `Code_peilbuis`,
`x_coordinaten_dwarsprofiel`), zodat `main.py` en `kaart_generator.py` ongewijzigd
blijven werken en de migratie aantoonbaar niets verandert. Zodra dat vaststaat
kunnen die kolommen weg en kan de lus in `main.py` rechtstreeks de lange tabel
lezen.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd
from pandas import DataFrame

from . import paths

SCHEMA_VERSIE_ONDERSTEUND = 1

PROFIEL_BLAD = "profielen"
PEILBUIS_BLAD = "peilbuizen"
PROJECT_BLAD = "project"

# Namen zoals `data_lader.bereid_metadata_peilbuis_voor` ze teruggeeft, zodat
# plotten.py niets merkt van de nieuwe indeling.
_METADATA_HERNOEM: dict[str, str] = {
    "code_peilbuis": "Code_peilbuis",
    "hoogte_bovenkant_peilbuis": "Hoogte_bovenkant_peilbuis",
    "bovenkant_filter_nap": "bovenkant_filter_M_NAP",
    "onderkant_filter_nap": "onderkant_filter_M_NAP",
}


@dataclass
class Bevinding:
    ernst: str  # 'fout' of 'waarschuwing'
    profiel_id: str
    melding: str

    def __str__(self) -> str:
        return f"[{self.ernst}] {self.profiel_id}: {self.melding}"


@dataclass
class ProjectInvoer:
    project: str
    workbook: Path
    algemene_config: dict[str, Any]
    profielen_df: DataFrame
    peilbuizen_df: DataFrame
    coords: dict[str, DataFrame]
    paden: dict[str, Path | None]


def _als_getal(waarde: Any) -> float | None:
    if waarde is None or (not isinstance(waarde, str) and pd.isna(waarde)):
        return None
    if isinstance(waarde, str):
        waarde = waarde.strip().replace(",", ".")
        if not waarde:
            return None
    return float(waarde)


def _tekst(waarde: Any) -> str:
    if waarde is None or (not isinstance(waarde, str) and pd.isna(waarde)):
        return ""
    return str(waarde).strip()


def _getal_als_tekst(waarde: Any) -> str:
    """Een getal terug naar tekst, zonder '.0' achter hele getallen."""
    getal: float | None = _als_getal(waarde)
    if getal is None:
        return ""
    if getal == int(getal):
        return str(int(getal))
    return repr(getal)


def laad_project(project: str, workbook: Path | None = None) -> ProjectInvoer:
    """Leest het werkboek en bouwt de structuren die de pipeline verwacht."""
    pad: Path = workbook or paths.workbook_pad(project)
    if not pad.is_file():
        raise FileNotFoundError(
            f"Werkboek niet gevonden: {pad}\n"
            f"Bouw het eenmalig met: uv run python scripts/bootstrap_workbook.py --project {project}"
        )

    bladen: list[str] = list(pd.ExcelFile(pad).sheet_names)

    project_df: DataFrame = pd.read_excel(pad, sheet_name=PROJECT_BLAD)
    algemene_config: dict[str, Any] = {
        _tekst(r["key"]): r["value"] for _, r in project_df.iterrows() if _tekst(r.get("key"))
    }

    versie: float | None = _als_getal(algemene_config.get("schema_versie"))
    if versie is not None and int(versie) != SCHEMA_VERSIE_ONDERSTEUND:
        raise ValueError(
            f"{pad.name} heeft schema_versie {int(versie)}, deze code ondersteunt "
            f"{SCHEMA_VERSIE_ONDERSTEUND}."
        )

    profielen: DataFrame = pd.read_excel(pad, sheet_name=PROFIEL_BLAD)
    peilbuizen: DataFrame = pd.read_excel(pad, sheet_name=PEILBUIS_BLAD)
    profielen["profiel_id"] = profielen["profiel_id"].map(_tekst)
    peilbuizen["profiel_id"] = peilbuizen["profiel_id"].map(_tekst)
    peilbuizen = peilbuizen.sort_values(["profiel_id", "positie"], kind="stable")

    coords: dict[str, DataFrame] = {}
    for profiel_id in profielen["profiel_id"]:
        blad: str = f"coords_{profiel_id}"
        if blad in bladen:
            coords[profiel_id] = _schoon_coords(pd.read_excel(pad, sheet_name=blad))

    return ProjectInvoer(
        project=project,
        workbook=pad,
        algemene_config=algemene_config,
        profielen_df=_bouw_legacy_profielen(profielen, peilbuizen),
        peilbuizen_df=peilbuizen,
        coords=coords,
        paden=_zoek_paden(project, algemene_config),
    )


def _schoon_coords(coords_df: DataFrame) -> DataFrame:
    """Zelfde opschoning als `data_lader.laad_profiel_data`: AutoCAD-plakwerk."""
    coords_df = coords_df[["x", "y"]].copy()
    for kolom in ("x", "y"):
        coords_df[kolom] = pd.to_numeric(
            coords_df[kolom].astype(str).str.strip().str.replace(",", ".", regex=False),
            errors="coerce",
        )
    coords_df.dropna(inplace=True)
    return coords_df


def _bouw_legacy_profielen(profielen: DataFrame, peilbuizen: DataFrame) -> DataFrame:
    """
    Zet de lange tabel om in de brede kolommen die `main.py` nu nog leest.

    Dit is de compatibiliteitslaag: `stijghoogte_namen` en `Code_peilbuis` worden
    komma-gescheiden, `x_coordinaten_dwarsprofiel` schuin-gescheiden, precies
    zoals de oude werkboeken ze bevatten.
    """
    rijen: list[dict[str, Any]] = []
    per_profiel = {pid: groep for pid, groep in peilbuizen.groupby("profiel_id")}

    for _, profiel in profielen.iterrows():
        profiel_id: str = _tekst(profiel["profiel_id"])
        groep: DataFrame = per_profiel.get(profiel_id, peilbuizen.iloc[0:0])

        rijen.append(
            {
                "profiel_id": profiel_id,
                "profiel_index": profiel.get("profiel_index"),
                "naam": _tekst(profiel["naam"]),
                "hoofdprofiel_id": _tekst(profiel.get("hoofdprofiel_id")),
                "diepte_niveau": _tekst(profiel.get("diepte_niveau")) or "hoofd",
                "stijghoogte_namen": ",".join(_tekst(v) for v in groep["peilbuis_naam"]),
                "Code_peilbuis": ",".join(_tekst(v) for v in groep["code_peilbuis"]),
                "x_coordinaten_dwarsprofiel": "/".join(
                    _getal_als_tekst(v) for v in groep["x_dwarsprofiel"]
                ),
                "x": _als_getal(profiel.get("x_rd")),
                "y": _als_getal(profiel.get("y_rd")),
                "teensloot_peil": _als_getal(profiel.get("teensloot_peil")),
                "stix_bestand": _tekst(profiel.get("stix_bestand")),
                "stix_offset_x_handmatig": _als_getal(profiel.get("stix_offset_x_handmatig")),
                "coords_sheet": f"coords_{profiel_id}"
                if _tekst(profiel.get("coords_blad"))
                else None,
                "actief": bool(profiel.get("actief", True)),
                "Opmerkingen": _tekst(profiel.get("opmerkingen")),
            }
        )
    return DataFrame(rijen)


def _zoek_paden(project: str, algemene_config: dict[str, Any]) -> dict[str, Path | None]:
    """Zoekt de bestanden waar het blad `project` naar verwijst."""
    paden: dict[str, Path | None] = {"stix_map": paths.stix_map(project)}
    for sleutel, naam in (
        ("peilbuisdata", "peilbuisdata_bestand"),
        ("neerslag", "neerslagdata_bestand"),
        ("terugkeertijden", "terugkeertijden_bestand"),
        ("buitenwaterstand", "buitenwaterstand_bestand"),
    ):
        bestandsnaam: str = _tekst(algemene_config.get(naam))
        if not bestandsnaam:
            paden[sleutel] = None
            continue
        try:
            paden[sleutel] = paths.zoek_invoerbestand(project, bestandsnaam)
        except FileNotFoundError:
            paden[sleutel] = None
    return paden


# --- toegang per profiel ---------------------------------------------------


def peilbuizen_van_profiel(invoer: ProjectInvoer, profiel_id: str) -> DataFrame:
    """De peilbuizen van één profiel, op positie gesorteerd."""
    groep: DataFrame = invoer.peilbuizen_df
    return groep[groep["profiel_id"] == profiel_id].sort_values("positie", kind="stable")


def metadata_voor_profiel(invoer: ProjectInvoer, profiel_id: str) -> DataFrame:
    """
    Dezelfde vier kolommen die `bereid_metadata_peilbuis_voor` oplevert.

    Vervangt het opzoeken via `metadata_pb_indices`: de waarden staan nu in het
    werkboek, dus een ingevoegde rij in een metadata-werkboek kan de profielen
    niet meer stil verschuiven.
    """
    groep: DataFrame = peilbuizen_van_profiel(invoer, profiel_id)
    kolommen: list[str] = [k for k in _METADATA_HERNOEM if k in groep.columns]
    return (
        groep[kolommen]
        .rename(columns=_METADATA_HERNOEM)
        .reset_index(drop=True)
    )


def laad_coords(invoer: ProjectInvoer, profiel_id: str) -> DataFrame:
    """De dwarsprofielgeometrie van één profiel."""
    if profiel_id not in invoer.coords:
        raise KeyError(
            f"Geen blad 'coords_{profiel_id}' in {invoer.workbook.name} "
            f"voor profiel {profiel_id}."
        )
    return invoer.coords[profiel_id]


def stix_pad(invoer: ProjectInvoer, profiel_id: str) -> Path | None:
    """
    Het stix-bestand van een profiel, uit de kolom `stix_bestand`.

    Vervangt de `DWP(\\d+)`-regex die in main.py en kaart_generator.py los van
    elkaar stond, en daarmee ook de eis dat `naam` precies 'Profiel <n>' is.
    """
    rij = invoer.profielen_df[invoer.profielen_df["profiel_id"] == profiel_id]
    if rij.empty:
        return None
    bestand: str = _tekst(rij.iloc[0].get("stix_bestand"))
    if not bestand:
        return None
    pad: Path = paths.stix_map(invoer.project) / bestand
    return pad if pad.is_file() else None


def valideer(invoer: ProjectInvoer) -> list[Bevinding]:
    """Controles die in de oude indeling nergens gedaan werden."""
    bevindingen: list[Bevinding] = []
    geldige_zones: set[str] = {"kruin", "berm", "teen"}
    profiel_ids: set[str] = set(invoer.profielen_df["profiel_id"])

    for _, profiel in invoer.profielen_df.iterrows():
        profiel_id: str = _tekst(profiel["profiel_id"])
        groep: DataFrame = peilbuizen_van_profiel(invoer, profiel_id)

        if groep.empty:
            bevindingen.append(Bevinding("fout", profiel_id, "geen peilbuizen"))
            continue

        hoofd: str = _tekst(profiel.get("hoofdprofiel_id"))
        if hoofd and hoofd not in profiel_ids:
            bevindingen.append(
                Bevinding("fout", profiel_id, f"hoofdprofiel_id '{hoofd}' bestaat niet")
            )

        for _, pb in groep.iterrows():
            zone: str = _tekst(pb.get("zone"))
            if zone and zone not in geldige_zones:
                bevindingen.append(
                    Bevinding("fout", profiel_id, f"onbekende zone '{zone}' bij {pb['peilbuis_naam']}")
                )
            elif not zone:
                bevindingen.append(
                    Bevinding(
                        "waarschuwing",
                        profiel_id,
                        f"zone leeg bij {pb['peilbuis_naam']}; de freatische lijn mist dan een anker",
                    )
                )

        x_waarden: list[float] = [
            v for v in (_als_getal(v) for v in groep["x_dwarsprofiel"]) if v is not None
        ]
        if x_waarden != sorted(x_waarden):
            bevindingen.append(
                Bevinding("waarschuwing", profiel_id, f"x_dwarsprofiel niet oplopend: {x_waarden}")
            )

        if _tekst(profiel.get("stix_bestand")) and stix_pad(invoer, profiel_id) is None:
            bevindingen.append(
                Bevinding(
                    "fout",
                    profiel_id,
                    f"stix-bestand '{profiel['stix_bestand']}' niet gevonden "
                    "(let op hoofdletters)",
                )
            )

        if _tekst(profiel.get("coords_blad")) and profiel_id not in invoer.coords:
            bevindingen.append(
                Bevinding("fout", profiel_id, f"blad coords_{profiel_id} ontbreekt")
            )

    vastgezet: DataFrame = invoer.peilbuizen_df
    if "handmatig_vastgezet" in vastgezet.columns:
        for _, pb in vastgezet.iterrows():
            if bool(pb.get("handmatig_vastgezet")) and not _tekst(pb.get("meetreeks_id")):
                bevindingen.append(
                    Bevinding(
                        "fout",
                        _tekst(pb["profiel_id"]),
                        f"{pb['peilbuis_naam']} is handmatig vastgezet maar heeft geen meetreeks_id",
                    )
                )
    return bevindingen
