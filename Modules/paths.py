# paths.py
"""
Eén plek voor alle paden en voor elke Unity Catalog-naam.

Lokaal en op Databricks dezelfde code: `repo_root()` zoekt omhoog naar
`pyproject.toml` en gebruikt bewust *niet* `__file__`, omdat Databricks
workspace-`.py`-bestanden met `exec` uitvoert en `__file__` dan niet bestaat.
"""

from pathlib import Path

# --- Unity Catalog: altijd drie-delige namen -------------------------------
# `spark.catalog.currentCatalog()` geeft de workspace-naam (dbw_hhnkda_prd_05)
# terug, niet een catalog waarop je kunt queryen. Een twee-delige naam vindt
# daarom niets. Zie docs/DATABRICKS_PERMISSIONS.md §7.

# Bron: alleen-lezen. Nooit naar gold_prd schrijven.
TBL_METING = "gold_prd.hl_grondwaterpeilmeting.meting"
TBL_MEETREEKS = "gold_prd.hl_grondwaterpeilmeting.meetreeks"
TBL_OBJECT = "gold_prd.hl_grondwaterpeilmeting.object"
TBL_VALIDATIESTATUS = "gold_prd.hl_grondwaterpeilmeting.validatiestatus"
TBL_VALIDATIERESULTAAT = "gold_prd.hl_grondwaterpeilmeting.validatieresultaat"
TBL_MEETLOCATIE = "gold_prd.hl_cs_objecten.meetlocatie"

# Eigen output: tabellen in het persoonlijke schema, bestanden op het volume.
UIT_CATALOG = "waterkeringbeheer_prd"
UIT_SCHEMA = "thanzen"
VOLUME_BASIS = (
    "/Volumes/waterkeringbeheer_prd/misc_files/misc_files/boxplots_pipeline"
)

# --- Mapnamen binnen de repo -----------------------------------------------
INVOER_MAP = "input_data"
BRON_MAP = "_bron"
STIX_MAP = "D-Stability"
PEILBUISDATA_MAP = "peilbuisdata"
EXTRA_MAP = "extra"
UITVOER_MAP = "Projecten"


def repo_root() -> Path:
    """Zoekt vanaf de werkmap omhoog naar de map met `pyproject.toml`."""
    for kandidaat in [Path.cwd(), *Path.cwd().parents]:
        if (kandidaat / "pyproject.toml").is_file():
            return kandidaat
    raise FileNotFoundError(
        f"Geen pyproject.toml gevonden vanaf {Path.cwd()} naar boven. "
        "Start vanuit de repo, of geef paden expliciet mee."
    )


def invoer_basis() -> Path:
    return repo_root() / INVOER_MAP


def bron_map(project: str) -> Path:
    """De oude config.yaml en werkboeken; herkomst, geen runtime-invoer."""
    return invoer_basis() / BRON_MAP / project


def projecten() -> list[str]:
    """Alle projectnamen: de mappen in input_data/, behalve `_bron`."""
    basis: Path = invoer_basis()
    if not basis.is_dir():
        return []
    return sorted(
        p.name for p in basis.iterdir() if p.is_dir() and p.name != BRON_MAP
    )


def project_map(project: str) -> Path:
    return invoer_basis() / project


def workbook_pad(project: str) -> Path:
    """Het handmatig onderhouden werkboek: input_data/<project>/<project>.xlsx."""
    return project_map(project) / f"{project}.xlsx"


def stix_map(project: str) -> Path:
    return project_map(project) / STIX_MAP


def peilbuisdata_map(project: str) -> Path:
    return project_map(project) / PEILBUISDATA_MAP


def extra_map(project: str) -> Path:
    return project_map(project) / EXTRA_MAP


def plots_map(project: str) -> Path:
    """Uitvoermap; wordt aangemaakt als die nog niet bestaat."""
    pad: Path = repo_root() / UITVOER_MAP / project / "plots"
    pad.mkdir(parents=True, exist_ok=True)
    return pad


def zoek_invoerbestand(project: str, bestandsnaam: str) -> Path:
    """
    Zoekt een invoerbestand in de projectmap, `extra/` en `peilbuisdata/`.

    Controleert de naam letterlijk tegen de maplijst, zodat een verschil in
    hoofdletters hier opvalt en niet pas op de cluster: NTFS is
    hoofdletterongevoelig, het bestandssysteem van de cluster niet.
    """
    kandidaten: list[Path] = [
        project_map(project) / bestandsnaam,
        extra_map(project) / bestandsnaam,
        peilbuisdata_map(project) / bestandsnaam,
    ]
    for pad in kandidaten:
        if pad.parent.is_dir() and bestandsnaam in {
            p.name for p in pad.parent.iterdir()
        }:
            return pad
    gezocht: str = "\n  ".join(str(p) for p in kandidaten)
    raise FileNotFoundError(
        f"'{bestandsnaam}' niet gevonden voor project '{project}'. Gezocht in:\n  {gezocht}"
    )


# --- Databricks-helpers ----------------------------------------------------


def uit_tabel(naam: str) -> str:
    """Volledige naam van een eigen tabel, bv. `boxplots_peilbuizen`."""
    return f"{UIT_CATALOG}.{UIT_SCHEMA}.boxplots_{naam}"


def volume_pad(*delen: str) -> str:
    """Pad op het Unity Catalog-volume onder de boxplots_pipeline-map."""
    return "/".join([VOLUME_BASIS, *delen])
