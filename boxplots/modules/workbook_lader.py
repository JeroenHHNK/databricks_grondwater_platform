# workbook_lader.py
"""
Laadt de invoer van een project uit de centrale invoer in `input_data/`:

- `config.csv`: één rij per peilbuis per dwarsprofiel, voor alle projecten.
  `LD_`-kolommen vullen we zelf in, `DD_`-kolommen komen uit Databricks (via
  `notebooks/bouw_koppeltabel_ld_dd.py --vul-config`);
- `profielen.csv`: één rij per dwarsprofiel (teenslootpeil, stix-offset,
  profiellijn in RD);
- `projecten.csv`: één rij per project (buitenwaterstand, boezempeiloffset);
- `D-Stability/<key>.stix` en `dwarsprofielen/<key>.csv` per dwarsprofiel.

Elk dwarsprofiel heet naar de dijkpaal waar het ligt (`BE_0072`); die sleutel
is uniek over alle projecten. Diepere peilbuizen in hetzelfde dwarsprofiel
hebben `diepte_niveau` A of B en worden hier een eigen profiel `BE_0072_A` met
`hoofdprofiel_id = BE_0072`.

Voor de bestaande pipeline bouwt deze module de oude tabellen terug: een lange
`peilbuizen_df` (één rij per peilbuis, met `positie` op volgorde van
`x_profiel`) en een `profielen_df` met de string-gebaseerde kolommen
(`stijghoogte_namen`, `Code_peilbuis`, `x_coordinaten_dwarsprofiel`), zodat
`profiel.py` en `kaart_generator.py` niets merken.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from pandas import DataFrame

from . import analyse, paths

# Getallen exact zoals ze in de csv staan; de standaardparser van read_csv
# wijkt soms een bit af (-0.9299999999999999 wordt -0.93).
CSV_FLOATS = "round_trip"
HOOFD = "hoofd"

# config.csv -> de kolomnamen die de pipeline sinds het werkboek gebruikt.
# De DD_-informatiekolommen (code, ligging en afstand van de meetlocatie)
# houden hun naam.
_CONFIG_HERNOEM: dict[str, str] = {
    "LD_cross_section_key": "dwarsprofiel",
    "LD_peilbuis": "peilbuis_naam",
    "LD_projectnummer": "projectnummer",
    "LD_code": "code_peilbuis",
    "LD_x_profiel": "x_dwarsprofiel",
    "LD_x_rd": "x_rd",
    "LD_y_rd": "y_rd",
    "LD_bk_peilbuis": "hoogte_bovenkant_peilbuis",
    "LD_bk_filter": "bovenkant_filter_nap",
    "LD_ok_filter": "onderkant_filter_nap",
    "DD_meetlocatie_id": "meetlocatie_id",
    "DD_meetreeks_id": "meetreeks_id",
    "DD_koppeling": "koppeling",
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
    "LD_project", "LD_cross_section_key", "LD_diepte_niveau", "LD_peilbuis",
    "LD_projectnummer", "LD_code",
    "DD_meetlocatie_id", "DD_meetreeks_id", "DD_koppeling", "DD_meetlocatie_code",
)


def lees_config() -> DataFrame:
    """`config.csv` voor alle projecten, met de kolomnamen van het bestand (LD_/DD_)."""
    pad: Path = paths.config_csv()
    if not pad.is_file():
        raise FileNotFoundError(f"Geen centrale config gevonden: {pad}")
    config: DataFrame = pd.read_csv(
        pad, float_precision=CSV_FLOATS, dtype={k: str for k in TEKST_KOLOMMEN}
    )
    for kolom in ("LD_project", "LD_cross_section_key", "LD_diepte_niveau", "LD_peilbuis"):
        config[kolom] = config[kolom].map(_tekst)
    config["LD_diepte_niveau"] = config["LD_diepte_niveau"].replace("", HOOFD)
    return config


LIJN_KOLOMMEN: tuple[str, ...] = (
    "lijn_x_start_rd", "lijn_y_start_rd", "lijn_x_eind_rd", "lijn_y_eind_rd",
)


def lees_profielen() -> DataFrame:
    """`profielen.csv`: één rij per dwarsprofiel, voor alle projecten (alles als tekst)."""
    pad: Path = paths.profielen_csv()
    if not pad.is_file():
        raise FileNotFoundError(f"Geen profielen.csv gevonden: {pad}")
    profielen: DataFrame = pd.read_csv(pad, dtype=str, keep_default_na=False)
    for kolom in ("project", "cross_section_key"):
        profielen[kolom] = profielen[kolom].map(_tekst)
    return profielen


def profiellijn(rij: Any) -> tuple[float, float, float, float] | None:
    """De profiellijn (x0, y0, x1, y1) in RD uit een rij van profielen.csv, of None."""
    waarden = [_als_getal(rij.get(k)) for k in LIJN_KOLOMMEN]
    if any(w is None for w in waarden):
        return None
    return tuple(waarden)  # type: ignore[return-value]


def projecteer_op_lijn(
    lijn: tuple[float, float, float, float], x_rd: float, y_rd: float
) -> tuple[float, float]:
    """(afstand langs de lijn vanaf het startpunt, afstand loodrecht op de lijn), in m."""
    x0, y0, x1, y1 = lijn
    dx, dy = x1 - x0, y1 - y0
    lengte: float = (dx * dx + dy * dy) ** 0.5
    ux, uy = dx / lengte, dy / lengte
    vx, vy = x_rd - x0, y_rd - y0
    return vx * ux + vy * uy, abs(ux * vy - uy * vx)


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
    config = config[config["LD_project"] == project]
    if config.empty:
        raise KeyError(f"Project {project} heeft geen rijen in {paths.config_csv().name}.")

    per_profiel: DataFrame = lees_profielen()
    per_profiel = per_profiel[per_profiel["project"] == project]
    peilbuizen: DataFrame = _peilbuizen_uit_config(config)
    profielen: DataFrame = _profielen_uit_peilbuizen(config, peilbuizen, per_profiel)

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
    pb.insert(0, "profiel_id", [profiel_id(k, d) for k, d in zip(pb["LD_cross_section_key"], pb["LD_diepte_niveau"])])
    pb = pb.sort_values(["profiel_id", "LD_x_profiel"], kind="stable")
    pb.insert(2, "positie", pb.groupby("profiel_id").cumcount() + 1)
    return (
        # diepte_niveau zit al in profiel_id en in profielen_df.
        pb.drop(columns=["LD_project", "LD_diepte_niveau"])
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


def _profielen_uit_peilbuizen(
    config: DataFrame, peilbuizen: DataFrame, profielen_csv: DataFrame
) -> DataFrame:
    """
    Eén rij per profiel, in de brede vorm die `profiel.py` leest.

    `stijghoogte_namen` en `Code_peilbuis` zijn komma-gescheiden,
    `x_coordinaten_dwarsprofiel` schuin-gescheiden, zoals in de oude werkboeken.
    Teenslootpeil, stix-offset en profiellijn komen uit `profielen.csv` en horen,
    net als stix en geometrie, bij het hoofdprofiel. Het kaartpunt is de
    peilbuis met de kleinste x in het hoofdprofiel.
    """
    per_profiel = {pid: groep for pid, groep in peilbuizen.groupby("profiel_id", sort=False)}
    eigen = {r["cross_section_key"]: r for _, r in profielen_csv.iterrows()}
    volgorde: DataFrame = config.drop_duplicates(["LD_cross_section_key", "LD_diepte_niveau"])
    rijen: list[dict[str, Any]] = []
    for _, r in volgorde.iterrows():
        sleutel: str = r["LD_cross_section_key"]
        niveau: str = r["LD_diepte_niveau"]
        pid: str = profiel_id(sleutel, niveau)
        groep: DataFrame = per_profiel[pid]
        hoofd: bool = niveau == HOOFD
        stix: Path = paths.stix_map() / f"{sleutel}.stix"
        p = eigen.get(sleutel, {})
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
                "teensloot_peil": _als_getal(p.get("teensloot_peil")) if hoofd else None,
                "stix_bestand": stix.name if hoofd and stix.is_file() else "",
                "stix_offset_x_handmatig": _als_getal(p.get("stix_offset_x")) if hoofd else None,
                **{k: (_als_getal(p.get(k)) if hoofd else None) for k in LIJN_KOLOMMEN},
                "coords_sheet": f"{paths.DWARSPROFIEL_MAP}/{sleutel}.csv" if hoofd else None,
                "actief": True,
                "dwarsprofiel": sleutel,
            }
        )
    return DataFrame(rijen)


def _zoek_paden(project: str, algemene_config: dict[str, Any]) -> dict[str, Path | None]:
    """
    De tijdreeksen die alle profielen van een project delen: de neerslag (één
    reeks voor alle projecten) en, als `projecten.csv` er een noemt, de
    buitenwaterstand in `tijdreeksen/`.
    """
    paden: dict[str, Path | None] = {
        "stix_map": paths.stix_map(),
        "neerslag": paths.neerslag_csv(),
        "buitenwaterstand": None,
    }
    bestandsnaam: str = _tekst(algemene_config.get("buitenwaterstand_bestand"))
    if bestandsnaam:
        pad: Path = paths.tijdreeks_map() / bestandsnaam
        if not pad.is_file():
            raise FileNotFoundError(f"{project}: buitenwaterstand_bestand {pad} bestaat niet")
        paden["buitenwaterstand"] = pad
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
    eigen: DataFrame = config[config["LD_project"] == invoer.project]

    # Plausibiliteit per dwarsprofiel (alle diepteniveaus samen).
    hoofdprofielen = invoer.profielen_df[invoer.profielen_df["diepte_niveau"] == HOOFD]
    for _, profiel in hoofdprofielen.iterrows():
        sleutel: str = profiel["profiel_id"]
        bevindingen += controleer_peilbuizen(
            sleutel,
            eigen[eigen["LD_cross_section_key"] == sleutel],
            invoer.coords.get(sleutel),
            profiellijn(profiel),
        )

    # Elk dwarsprofiel in config.csv heeft een rij in profielen.csv, en andersom.
    in_config: set[str] = set(eigen["LD_cross_section_key"])
    profielen: DataFrame = lees_profielen()
    in_profielen: set[str] = set(profielen.loc[profielen["project"] == invoer.project, "cross_section_key"])
    for sleutel in sorted(in_config - in_profielen):
        bevindingen.append(Bevinding(
            "fout", sleutel, f"staat niet in {paths.profielen_csv().name}; het teenslootpeil ontbreekt dan"))
    for sleutel in sorted(in_profielen - in_config):
        bevindingen.append(Bevinding(
            "waarschuwing", sleutel, f"staat in {paths.profielen_csv().name} maar heeft geen peilbuizen in config.csv"))
    for sleutel in sorted(set(profielen.loc[profielen["cross_section_key"].duplicated(), "cross_section_key"]) & in_config):
        bevindingen.append(Bevinding("fout", sleutel, f"staat dubbel in {paths.profielen_csv().name}"))

    projecten_per_sleutel = config.groupby("LD_cross_section_key")["LD_project"].unique()
    for sleutel in sorted(set(eigen["LD_cross_section_key"])):
        anderen = [p for p in projecten_per_sleutel[sleutel] if p != invoer.project]
        if anderen:
            bevindingen.append(
                Bevinding("fout", sleutel, f"sleutel staat ook in project {', '.join(anderen)}")
            )

    rijsleutel: list[str] = ["LD_cross_section_key", "LD_diepte_niveau", "LD_peilbuis"]
    for _, rij in eigen[eigen.duplicated(rijsleutel)].iterrows():
        bevindingen.append(
            Bevinding("fout", rij["LD_cross_section_key"], f"{rij['LD_peilbuis']} staat dubbel in config.csv")
        )

    # Eén peilbuis kan in twee dwarsprofielen staan; één meetlocatie bij twee peilbuizen niet.
    putten = (
        config.dropna(subset=["DD_meetlocatie_id"])
        .drop_duplicates(["DD_meetlocatie_id", "LD_project", "LD_peilbuis"])["DD_meetlocatie_id"]
    )
    dubbel_id: set[str] = set(putten[putten.duplicated()])
    for _, rij in eigen[eigen["DD_meetlocatie_id"].isin(dubbel_id)].iterrows():
        bevindingen.append(
            Bevinding("fout", rij["LD_cross_section_key"],
                      f"DD_meetlocatie_id van {rij['LD_peilbuis']} hoort ook bij een andere peilbuis")
        )
    return bevindingen


# Grenzen voor de plausibiliteitscontroles (zie README, "Wat de controles betekenen").
# Een put in een straatpot kan een paar decimeter onder maaiveld eindigen.
BOVENKANT_BOVEN_MAAIVELD_M: tuple[float, float] = (-0.5, 1.5)
MAX_AFSTAND_TOT_LIJN_M: float = 5.0
MAX_VERSCHIL_PROJECTIE_M: float = 0.5
_ZONE_VOLGORDE: dict[str, int] = {"kruin": 0, "berm": 1, "teen": 2}


def controleer_peilbuizen(
    sleutel: str,
    rijen: DataFrame,
    geometrie: DataFrame | None,
    lijn: tuple[float, float, float, float] | None,
) -> list[Bevinding]:
    """
    Plausibiliteit van de peilbuizen van één dwarsprofiel.

    `rijen` zijn rijen in de vorm van `config.csv` (LD_-kolommen), alle
    diepteniveaus samen; `geometrie` is het dijkprofiel (x, y) en `lijn` de
    profiellijn in RD uit `profielen.csv`. Wordt gebruikt door `valideer()` en
    door `notebooks/nieuw_dwarsprofiel.py`, zodat beide hetzelfde zeggen.
    """
    uit: list[Bevinding] = []
    x_min = x_max = None
    if geometrie is not None and not geometrie.empty:
        geo = geometrie.sort_values("x")
        x_min, x_max = float(geo["x"].min()), float(geo["x"].max())

    for _, r in rijen.iterrows():
        naam: str = f"{_tekst(r.get('LD_peilbuis'))} ({_tekst(r.get('LD_diepte_niveau')) or HOOFD})"
        x = _als_getal(r.get("LD_x_profiel"))
        bk_pb = _als_getal(r.get("LD_bk_peilbuis"))
        bk_f = _als_getal(r.get("LD_bk_filter"))
        ok_f = _als_getal(r.get("LD_ok_filter"))

        if bk_f is not None and ok_f is not None and bk_f <= ok_f:
            uit.append(Bevinding("fout", sleutel, f"{naam}: bovenkant filter {bk_f} ligt niet boven onderkant {ok_f}"))
        if bk_f is not None and bk_pb is not None and bk_f > bk_pb:
            uit.append(Bevinding(
                "fout", sleutel,
                f"{naam}: bovenkant filter {bk_f} ligt boven de bovenkant peilbuis {bk_pb}; "
                "filters of putten verwisseld?",
            ))
        if x is not None and x_min is not None:
            if not x_min <= x <= x_max:
                uit.append(Bevinding("fout", sleutel, f"{naam}: x {x} ligt buiten de geometrie ({x_min:g} tot {x_max:g})"))
            elif bk_pb is not None:
                maaiveld = float(np.interp(x, geo["x"], geo["y"]))
                boven: float = bk_pb - maaiveld
                laag, hoog = BOVENKANT_BOVEN_MAAIVELD_M
                if not laag <= boven <= hoog:
                    uit.append(Bevinding(
                        "waarschuwing", sleutel,
                        f"{naam}: bovenkant peilbuis {bk_pb} staat {boven:+.2f} m t.o.v. het maaiveld "
                        f"({maaiveld:.2f}) op x {x}; verwacht {laag:+g} tot {hoog:+g} m. "
                        "Klopt x, of de bovenkant?",
                    ))
        if lijn is not None and x is not None:
            x_rd, y_rd = _als_getal(r.get("LD_x_rd")), _als_getal(r.get("LD_y_rd"))
            if x_rd is not None and y_rd is not None:
                langs, loodrecht = projecteer_op_lijn(lijn, x_rd, y_rd)
                if loodrecht > MAX_AFSTAND_TOT_LIJN_M:
                    uit.append(Bevinding(
                        "waarschuwing", sleutel, f"{naam}: put staat {loodrecht:.1f} m naast de profiellijn"))
                if abs(langs - x) > MAX_VERSCHIL_PROJECTIE_M:
                    uit.append(Bevinding(
                        "waarschuwing", sleutel,
                        f"{naam}: x {x} wijkt {langs - x:+.2f} m af van de projectie van de RD-coördinaten ({langs:.2f})",
                    ))

    # Volgorde langs het profiel: kruin, dan berm, dan teen.
    hoofd = rijen[rijen["LD_diepte_niveau"].map(_tekst).isin(["", HOOFD])].copy()
    hoofd["_x"] = hoofd["LD_x_profiel"].map(_als_getal)
    hoofd["_zone"] = hoofd["LD_code"].map(analyse.classificeer_peilbuis_zone)
    hoofd = hoofd.dropna(subset=["_x", "_zone"]).sort_values("_x")
    rang = hoofd["_zone"].map(_ZONE_VOLGORDE).tolist()
    if rang != sorted(rang):
        volgorde = ", ".join(f"{p} ({z})" for p, z in zip(hoofd["LD_peilbuis"], hoofd["_zone"]))
        uit.append(Bevinding(
            "waarschuwing", sleutel,
            f"zones staan niet in de volgorde kruin, berm, teen langs x: {volgorde}",
        ))
    return uit


def losse_bestanden() -> list[Path]:
    """Stix- en geometriebestanden waarvan de sleutel niet in config.csv staat."""
    sleutels: set[str] = set(lees_config()["LD_cross_section_key"])
    return [
        pad
        for map_, patroon in ((paths.stix_map(), "*.stix"), (paths.dwarsprofiel_map(), "*.csv"))
        for pad in sorted(map_.glob(patroon))
        if pad.stem not in sleutels
    ]
