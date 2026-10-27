# workbook_lader.py
"""
Laadt de invoer van een project uit de centrale invoer in `input_data/`:

- `config.csv`: één rij per peilbuis per dwarsprofiel, voor alle projecten;
- `projecten.csv`: één rij per project (buitenwaterstand, bestandsnamen);
- `D-Stability/<key>.stix` en `dwarsprofielen/<key>.csv` per dwarsprofiel.

Elk dwarsprofiel heet naar de dijkpaal waar het ligt (`BE_0072`); die sleutel
is uniek over alle projecten. Diepere peilbuizen in hetzelfde dwarsprofiel
hebben `diepte_niveau` A of B en worden hier een eigen profiel `BE_0072_A` met
`hoofdprofiel_id = BE_0072`.

Voor de bestaande pipeline bouwt deze module de oude tabellen terug: een lange
`peilbuizen_df` (één rij per peilbuis, met `positie` op volgorde van
`x_profiel`) en een `profielen_df` met de string-gebaseerde kolommen
(`stijghoogte_namen`, `Code_peilbuis`, `x_coordinaten_dwarsprofiel`), zodat
`main.py`, `profiel.py` en `kaart_generator.py` niets merken.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd
from pandas import DataFrame

from . import analyse, paths

# Getallen exact zoals ze in de csv staan; de standaardparser van read_csv
# wijkt soms een bit af (-0.9299999999999999 wordt -0.93).
CSV_FLOATS = "round_trip"
HOOFD = "hoofd"

# config.csv -> de kolomnamen die de pipeline sinds het werkboek gebruikt.
_CONFIG_HERNOEM: dict[str, str] = {
    "cross_section_key": "dwarsprofiel",
    "peilbuis": "peilbuis_naam",
    "code": "code_peilbuis",
    "x_profiel": "x_dwarsprofiel",
    "bk_peilbuis": "hoogte_bovenkant_peilbuis",
    "bk_filter": "bovenkant_filter_nap",
    "ok_filter": "onderkant_filter_nap",
}

# De kolomnamen die plotten.py verwacht.
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


def _waarde(waarde: Any) -> Any:
    """Een waarde uit projecten.csv: een getal als het er een is, anders de tekst."""
    if isinstance(waarde, str):
        try:
            return float(waarde)
        except ValueError:
            return waarde
    return waarde


TEKST_KOLOMMEN: tuple[str, ...] = (
    "project", "cross_section_key", "diepte_niveau", "peilbuis",
    "projectnummer", "meetlocatie_id", "koppeling", "code",
)


def lees_config() -> DataFrame:
    """`config.csv` voor alle projecten."""
    pad: Path = paths.config_csv()
    if not pad.is_file():
        raise FileNotFoundError(f"Geen centrale config gevonden: {pad}")
    config: DataFrame = pd.read_csv(
        pad, float_precision=CSV_FLOATS, dtype={k: str for k in TEKST_KOLOMMEN}
    )
    for kolom in ("project", "cross_section_key", "diepte_niveau", "peilbuis"):
        config[kolom] = config[kolom].map(_tekst)
    config["diepte_niveau"] = config["diepte_niveau"].replace("", HOOFD)
    return config


def profiel_id(sleutel: str, diepte_niveau: str) -> str:
    """`BE_0072` voor het hoofdprofiel, `BE_0072_A` voor de diepere peilbuizen."""
    return sleutel if diepte_niveau == HOOFD else f"{sleutel}_{diepte_niveau}"


def laad_project(project: str) -> ProjectInvoer:
    """Leest de centrale invoer van één project en bouwt de structuren die de pipeline verwacht."""
    projecten: DataFrame = pd.read_csv(paths.projecten_csv(), dtype=str)
    rij: DataFrame = projecten[projecten["project"].str.strip() == project]
    if rij.empty:
        raise KeyError(f"Project {project} staat niet in {paths.projecten_csv().name}.")
    algemene_config: dict[str, Any] = {
        k: _waarde(v) for k, v in rij.iloc[0].items() if k != "project" and pd.notna(v)
    }

    config: DataFrame = lees_config()
    config = config[config["project"] == project]
    if config.empty:
        raise KeyError(f"Project {project} heeft geen rijen in {paths.config_csv().name}.")

    peilbuizen: DataFrame = _peilbuizen_uit_config(config)
    profielen: DataFrame = _profielen_uit_peilbuizen(config, peilbuizen)

    coords: dict[str, DataFrame] = {}
    for _, profiel in profielen.iterrows():
        bestand: str = _tekst(profiel["coords_sheet"])
        if bestand and (paths.invoer_basis() / bestand).is_file():
            coords[profiel["profiel_id"]] = _schoon_coords(
                pd.read_csv(paths.invoer_basis() / bestand, float_precision=CSV_FLOATS)
            )

    return ProjectInvoer(
        project=project,
        workbook=paths.config_csv(),
        algemene_config=algemene_config,
        profielen_df=profielen,
        peilbuizen_df=peilbuizen,
        coords=coords,
        paden=_zoek_paden(project, algemene_config),
    )


def _peilbuizen_uit_config(config: DataFrame) -> DataFrame:
    """Eén rij per peilbuis per profiel, met `profiel_id` en `positie` (volgorde van x)."""
    pb: DataFrame = config.copy()
    pb.insert(0, "profiel_id", [profiel_id(k, d) for k, d in zip(pb["cross_section_key"], pb["diepte_niveau"])])
    pb = pb.sort_values(["profiel_id", "x_profiel"], kind="stable")
    pb.insert(2, "positie", pb.groupby("profiel_id").cumcount() + 1)
    return (
        # diepte_niveau zit al in profiel_id en in profielen_df.
        pb.drop(columns=["project", "diepte_niveau", "teensloot_peil", "stix_offset_x"])
        .rename(columns=_CONFIG_HERNOEM)
        .reset_index(drop=True)
    )


def _schoon_coords(coords_df: DataFrame) -> DataFrame:
    """x/y als getal; AutoCAD-plakwerk heeft soms tekst met (vaste) spaties erachter."""
    coords_df = coords_df[["x", "y"]].copy()
    for kolom in ("x", "y"):
        coords_df[kolom] = pd.to_numeric(
            coords_df[kolom].astype(str).str.strip().str.replace(",", ".", regex=False),
            errors="coerce",
        )
    coords_df.dropna(inplace=True)
    return coords_df


def _profielen_uit_peilbuizen(config: DataFrame, peilbuizen: DataFrame) -> DataFrame:
    """
    Eén rij per profiel, in de brede vorm die `main.py` en `profiel.py` lezen.

    `stijghoogte_namen` en `Code_peilbuis` zijn komma-gescheiden,
    `x_coordinaten_dwarsprofiel` schuin-gescheiden, zoals in de oude werkboeken.
    Teenslootpeil, stix-offset, stix en geometrie horen bij het hoofdprofiel; het
    kaartpunt is de eerste peilbuis van het hoofdprofiel.
    """
    per_profiel = {pid: groep for pid, groep in peilbuizen.groupby("profiel_id", sort=False)}
    volgorde: DataFrame = config.drop_duplicates(["cross_section_key", "diepte_niveau"])
    rijen: list[dict[str, Any]] = []
    for _, r in volgorde.iterrows():
        sleutel: str = r["cross_section_key"]
        niveau: str = r["diepte_niveau"]
        pid: str = profiel_id(sleutel, niveau)
        groep: DataFrame = per_profiel[pid]
        hoofd: bool = niveau == HOOFD
        stix: Path = paths.stix_map() / f"{sleutel}.stix"
        rijen.append(
            {
                "profiel_id": pid,
                "naam": sleutel if hoofd else f"{sleutel} {niveau}",
                "hoofdprofiel_id": "" if hoofd else sleutel,
                "diepte_niveau": niveau,
                "stijghoogte_namen": ",".join(_tekst(v) for v in groep["peilbuis_naam"]),
                "Code_peilbuis": ",".join(_tekst(v) for v in groep["code_peilbuis"]),
                "x_coordinaten_dwarsprofiel": "/".join(
                    _getal_als_tekst(v) for v in groep["x_dwarsprofiel"]
                ),
                "x": _als_getal(groep["x_rd"].iloc[0]) if hoofd else None,
                "y": _als_getal(groep["y_rd"].iloc[0]) if hoofd else None,
                "teensloot_peil": _als_getal(r["teensloot_peil"]) if hoofd else None,
                "stix_bestand": stix.name if hoofd and stix.is_file() else "",
                "stix_offset_x_handmatig": _als_getal(r["stix_offset_x"]) if hoofd else None,
                "coords_sheet": f"{paths.DWARSPROFIEL_MAP}/{sleutel}.csv" if hoofd else None,
                "actief": True,
                "dwarsprofiel": sleutel,
            }
        )
    return DataFrame(rijen)


def _zoek_paden(project: str, algemene_config: dict[str, Any]) -> dict[str, Path | None]:
    """
    Zoekt de bestanden waar `projecten.csv` naar verwijst.

    `peilbuisdata` is de export (xlsx) in `peilbuisdata/`; `reeksen` de map met
    één csv per peilbuis, als die er is.
    """
    paden: dict[str, Path | None] = {"stix_map": paths.stix_map()}
    for sleutel, naam in (
        ("peilbuisdata", "peilbuisdata_bestand"),
        ("neerslag", "neerslagdata_bestand"),
        ("terugkeertijden", "terugkeertijden_bestand"),
        ("buitenwaterstand", "buitenwaterstand_bestand"),
    ):
        bestandsnaam: str = _tekst(algemene_config.get(naam))
        paden[sleutel] = None
        if not bestandsnaam:
            continue
        if sleutel == "peilbuisdata":
            pad: Path = paths.peilbuisdata_map(project) / bestandsnaam
            paden[sleutel] = pad if pad.is_file() else None
        else:
            try:
                paden[sleutel] = paths.zoek_invoerbestand(project, bestandsnaam)
            except FileNotFoundError:
                pass

    reeksen: Path = paths.peilbuisdata_map(project)
    paden["reeksen"] = reeksen if reeksen.is_dir() and any(reeksen.glob("*.csv")) else None
    return paden


# --- toegang per profiel ---------------------------------------------------


def peilbuizen_van_profiel(invoer: ProjectInvoer, profiel_id: str) -> DataFrame:
    """De peilbuizen van één profiel, op positie gesorteerd."""
    groep: DataFrame = invoer.peilbuizen_df
    return groep[groep["profiel_id"] == profiel_id].sort_values("positie", kind="stable")


def metadata_voor_profiel(invoer: ProjectInvoer, profiel_id: str) -> DataFrame:
    """
    De vier kolommen die plotten.py per peilbuis nodig heeft, op positie.
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
            f"Geen dwarsprofielgeometrie voor profiel {profiel_id} in {invoer.project}."
        )
    return invoer.coords[profiel_id]


def stix_pad(invoer: ProjectInvoer, profiel_id: str) -> Path | None:
    """
    Het stix-bestand van een profiel: `D-Stability/<dwarsprofiel>.stix`, als dat er is.
    """
    rij = invoer.profielen_df[invoer.profielen_df["profiel_id"] == profiel_id]
    if rij.empty:
        return None
    bestand: str = _tekst(rij.iloc[0].get("stix_bestand"))
    if not bestand:
        return None
    pad: Path = paths.stix_map() / bestand
    return pad if pad.is_file() else None


def valideer(invoer: ProjectInvoer) -> list[Bevinding]:
    """Controles op de invoer van één project, en op wat het met andere projecten deelt."""
    bevindingen: list[Bevinding] = []

    for _, profiel in invoer.profielen_df.iterrows():
        pid: str = _tekst(profiel["profiel_id"])
        for _, pb in peilbuizen_van_profiel(invoer, pid).iterrows():
            if analyse.classificeer_peilbuis_zone(pb["code_peilbuis"]) is None:
                bevindingen.append(
                    Bevinding(
                        "waarschuwing",
                        pid,
                        f"geen zone (BUKR, BIT, ...) in de code van {pb['peilbuis_naam']}; "
                        "de freatische lijn mist dan een anker",
                    )
                )

        if profiel["diepte_niveau"] != HOOFD:
            continue
        if not profiel["stix_bestand"]:
            bevindingen.append(
                Bevinding("waarschuwing", pid,
                          f"geen {paths.STIX_MAP}/{pid}.stix; de plot krijgt geen grondopbouw")
            )
        if pid not in invoer.coords:
            bevindingen.append(Bevinding("fout", pid, f"{profiel['coords_sheet']} ontbreekt"))

    config: DataFrame = lees_config()
    eigen: DataFrame = config[config["project"] == invoer.project]

    projecten_per_sleutel = config.groupby("cross_section_key")["project"].unique()
    for sleutel in sorted(set(eigen["cross_section_key"])):
        anderen = [p for p in projecten_per_sleutel[sleutel] if p != invoer.project]
        if anderen:
            bevindingen.append(
                Bevinding("fout", sleutel, f"sleutel staat ook in project {', '.join(anderen)}")
            )

    rijsleutel: list[str] = ["cross_section_key", "diepte_niveau", "peilbuis"]
    for _, rij in eigen[eigen.duplicated(rijsleutel)].iterrows():
        bevindingen.append(
            Bevinding("fout", rij["cross_section_key"], f"{rij['peilbuis']} staat dubbel in config.csv")
        )

    # Eén peilbuis kan in twee dwarsprofielen staan; één meetlocatie bij twee peilbuizen niet.
    putten = (
        config.dropna(subset=["meetlocatie_id"])
        .drop_duplicates(["meetlocatie_id", "project", "peilbuis"])["meetlocatie_id"]
    )
    dubbel_id: set[str] = set(putten[putten.duplicated()])
    for _, rij in eigen[eigen["meetlocatie_id"].isin(dubbel_id)].iterrows():
        bevindingen.append(
            Bevinding("fout", rij["cross_section_key"],
                      f"meetlocatie_id van {rij['peilbuis']} hoort ook bij een andere peilbuis")
        )
    return bevindingen


def losse_bestanden() -> list[Path]:
    """Stix- en geometriebestanden waarvan de sleutel niet in config.csv staat."""
    sleutels: set[str] = set(lees_config()["cross_section_key"])
    return [
        pad
        for map_, patroon in ((paths.stix_map(), "*.stix"), (paths.dwarsprofiel_map(), "*.csv"))
        for pad in sorted(map_.glob(patroon))
        if pad.stem not in sleutels
    ]
