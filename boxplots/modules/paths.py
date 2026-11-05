# paths.py
"""
Eén plek voor alle paden en voor elke Unity Catalog-naam.

Lokaal en op Databricks dezelfde code: `repo_root()` gaat uit van de plek van
deze module en valt terug op omhoog zoeken naar `pyproject.toml` vanaf de
werkmap, omdat Databricks workspace-`.py`-bestanden met `exec` uitvoert en
`__file__` dan niet bestaat.
"""

from pathlib import Path

# --- Unity Catalog: altijd drie-delige namen -------------------------------
# `spark.catalog.currentCatalog()` geeft de workspace-naam (dbw_hhnkda_prd_05)
# terug, niet een catalog waarop je kunt queryen. Een twee-delige naam vindt
# daarom niets. Zie docs/DATABRICKS_PERMISSIONS.md §7.

# Bron: alleen-lezen. Nooit naar gold_prd schrijven.
TBL_METING = "gold_prd.hl_grondwaterpeilmeting.meting"
TBL_MEETREEKS = "gold_prd.hl_grondwaterpeilmeting.meetreeks"
TBL_VALIDATIESTATUS = "gold_prd.hl_grondwaterpeilmeting.validatiestatus"
TBL_VALIDATIERESULTAAT = "gold_prd.hl_grondwaterpeilmeting.validatieresultaat"
TBL_MEETLOCATIE = "gold_prd.hl_cs_objecten.meetlocatie"

# --- Mapnamen binnen de repo -----------------------------------------------
INVOER_MAP = "input_data"
STIX_MAP = "D-Stability"
DWARSPROFIEL_MAP = "dwarsprofielen"
TIJDREEKS_MAP = "tijdreeksen"
EXPORT_MAP = "databricks_export"
CONTROLE_MAP = "controle"
UITVOER_MAP = "Projecten"
NEERSLAG_CSV = "neerslag_berkhout.csv"


def repo_root() -> Path:
    """
    De map `boxplots/` (die met `pyproject.toml` en `input_data/`).

    Eerst vanaf deze module: zo vindt de centrale app in de repo-root, die vanuit
    een andere werkmap start, toch `boxplots/`. Lukt dat niet (`__file__` ontbreekt
    als Databricks een workspace-bestand met `exec` uitvoert), dan vanaf de werkmap
    omhoog naar de eerste map met `pyproject.toml`.
    """
    try:
        hier = Path(__file__).resolve().parent.parent
    except NameError:
        hier = None
    if hier is not None and (hier / "pyproject.toml").is_file() and (hier / INVOER_MAP).is_dir():
        return hier
    for kandidaat in [Path.cwd(), *Path.cwd().parents]:
        if (kandidaat / "pyproject.toml").is_file():
            return kandidaat
    raise FileNotFoundError(
        f"Geen pyproject.toml gevonden vanaf {Path.cwd()} naar boven. "
        "Start vanuit de repo, of geef paden expliciet mee."
    )


def invoer_basis() -> Path:
    return repo_root() / INVOER_MAP


def projecten() -> list[str]:
    """Alle projectnamen, uit projecten.csv."""
    pad: Path = projecten_csv()
    if not pad.is_file():
        return []
    import pandas as pd  # alleen hier nodig; paths blijft verder zonder pandas

    return sorted(pd.read_csv(pad, usecols=["project"], dtype=str)["project"].str.strip())


# --- met de hand onderhouden ------------------------------------------------
# Alles wat per dwarsprofiel is, staat voor alle projecten samen; de sleutel
# (`BE_0072`) is uniek over alle projecten.


def config_csv() -> Path:
    """Eén rij per peilbuis per dwarsprofiel, voor alle projecten."""
    return invoer_basis() / "config.csv"


def profielen_csv() -> Path:
    """Eén rij per dwarsprofiel: teenslootpeil, stix-offset, profiellijn in RD."""
    return invoer_basis() / "profielen.csv"


def projecten_csv() -> Path:
    """Eén rij per project: buitenwaterstand, boezempeiloffset."""
    return invoer_basis() / "projecten.csv"


def dwarsprofiel_map() -> Path:
    """De dwarsprofielgeometrie, één csv per dwarsprofiel: dwarsprofielen/BE_0072.csv."""
    return invoer_basis() / DWARSPROFIEL_MAP


def stix_map() -> Path:
    """De D-Stability-bestanden, één per dwarsprofiel: D-Stability/BE_0072.stix."""
    return invoer_basis() / STIX_MAP


def tijdreeks_map() -> Path:
    """Neerslag en buitenwaterstanden: wat niet per peilbuis is."""
    return invoer_basis() / TIJDREEKS_MAP


def neerslag_csv() -> Path:
    """Eén neerslagreeks (Berkhout) voor alle projecten."""
    return tijdreeks_map() / NEERSLAG_CSV


# --- gegenereerd ------------------------------------------------------------


def export_map() -> Path:
    """Wat de db_notebooks uit Databricks halen."""
    return invoer_basis() / EXPORT_MAP


def controle_map() -> Path:
    """Wat de controlescripts schrijven: de koppeltabel en te_controleren.csv."""
    return invoer_basis() / CONTROLE_MAP


def plots_map(project: str) -> Path:
    """Uitvoermap van maak_plots.py; wordt aangemaakt als die nog niet bestaat."""
    pad: Path = repo_root() / UITVOER_MAP / project / "plots"
    pad.mkdir(parents=True, exist_ok=True)
    return pad
