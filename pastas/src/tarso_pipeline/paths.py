"""Locating the repo and the forcing files it carries.

The forcing series are small (~58 KB for 2020-to-date) and change rarely, so they
travel **with the code** in ``data/forcing/`` rather than living in object storage.
That makes a Git folder pull the whole deployment: pull the repo on Databricks and
the data is there, at a path every notebook can compute rather than hardcode.

``repo_root`` deliberately does not use ``__file__``. Databricks ``exec``s a
workspace .py instead of running it as a script, leaving ``__file__`` undefined, so
anything that depends on it breaks the moment it is opened in the workspace.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

FORCING_DIRNAME = "data/forcing"


def repo_root(start: str | Path | None = None) -> Path:
    """Find the repo root by walking up for the pyproject.

    An explicit ``start`` is searched and nothing else, so a caller can point at
    another tree (a test fixture, a second checkout) and get a straight answer.
    With no ``start``, the module's own location is tried first and the working
    directory second, which covers being imported, being ``exec``'d, and being
    run from any sub-directory.
    """
    if start is not None:
        candidates = [Path(start).resolve()]
    else:
        candidates = []
        try:
            candidates.append(Path(__file__).resolve().parent.parent.parent)
        except NameError:  # exec'd, e.g. by Databricks
            pass
        candidates.append(Path.cwd())

    for candidate in candidates:
        for directory in [candidate, *candidate.parents]:
            if (directory / "pyproject.toml").is_file():
                return directory
    raise RuntimeError(
        "Cannot locate the repo root: no pyproject.toml at or above "
        f"{candidates[-1]}. Is this running inside the repo / Git folder?"
    )


def forcing_dir(root: str | Path | None = None) -> Path:
    """The directory holding the committed forcing CSVs."""
    return (Path(root) if root else repo_root()) / FORCING_DIRNAME


def forcing_path(station: int, root: str | Path | None = None) -> Path:
    """Path of the committed forcing file for one KNMI station, inside the repo.

    One file per station, both variables in it: the column selects the variable,
    so precipitation and evaporation do not need separate files.
    """
    from tarso_pipeline.forcing import forcing_filename

    return forcing_dir(root) / forcing_filename(station)


# --- Databricks write targets (handover 2026-10-14, established empirically) ---
#
# The source catalog gold_prd is READ-ONLY: SELECT/EXECUTE/READ VOLUME only, no
# CREATE TABLE, no CREATE SCHEMA, no WRITE VOLUME. Never write there.
#
# Tables and model files go to different schemas of the same catalog, and that
# split is deliberate -- do not consolidate them.

SOURCE_TABLE = "gold_prd.hl_grondwaterpeilmeting.meting"

OUTPUT_CATALOG = "waterkeringbeheer_prd"
OUTPUT_SCHEMA = "jvanruitenbeek"
MANIFEST_TABLE = f"{OUTPUT_CATALOG}.{OUTPUT_SCHEMA}.pastas_series_manifest"
METRICS_TABLE = f"{OUTPUT_CATALOG}.{OUTPUT_SCHEMA}.pastas_fit_metrics"
CHECKS_TABLE = f"{OUTPUT_CATALOG}.{OUTPUT_SCHEMA}.pastas_model_checks"

# WRITE VOLUME here is a separate grant from the table write that was tested;
# confirm it once with a throwaway file before a batch run.
MODEL_VOLUME_BASE = (
    f"/Volumes/{OUTPUT_CATALOG}/misc_files/misc_files/pastas_pipeline"
)

# The forcing CSV lives beside the models on the same volume. One file per
# station; the filename is derived from the station, never hardcoded, so
# per-series stations later are a change of argument.
FORCING_VOLUME_DIR = f"{MODEL_VOLUME_BASE}/knmi_forcing"


def forcing_volume_path(station: int | str, base: str | None = None) -> str:
    """Path of one station's forcing CSV on the volume."""
    from tarso_pipeline.forcing import forcing_file

    return forcing_file(base or FORCING_VOLUME_DIR, station)


def model_dir_for(series_name: str, base: str | None = None) -> str:
    """One folder per series under the models volume."""
    from tarso_pipeline.naming import sanitize_name

    safe = sanitize_name(series_name)
    if not safe:
        raise ValueError(f"series_name sanitises to nothing: {series_name!r}")
    return f"{base or MODEL_VOLUME_BASE}/{safe}"


# --- run folder layout ------------------------------------------------------
#
# One dated folder per run, with artefacts grouped by kind rather than by
# series, so all 20 .pas files sit together and all 20 plots sit together:
#
#   <volume>/run_26_10_15/
#       pas_files/          series_<hash>_<model>.pas
#       results_files/      series_<hash>_<model>_results.png
#       diagnostics_files/  series_<hash>_<model>_diagnostics.png

PAS_DIR = "pas_files"
RESULTS_DIR = "results_files"
DIAGNOSTICS_DIR = "diagnostics_files"
ARTEFACT_DIRS = (PAS_DIR, RESULTS_DIR, DIAGNOSTICS_DIR)

# Characters of the meetreeks_id hash kept in a series name. 16 is already
# collision-safe for this many series; 24 costs nothing and leaves more margin.
SERIES_ID_CHARS = 24


def series_name_from_id(meetreeks_id: str, chars: int = SERIES_ID_CHARS) -> str:
    """``series_<first N hex of meetreeks_id>``.

    The full id stays on every metrics row, so a downstream join on this prefix
    recovers it.
    """
    if not meetreeks_id:
        raise ValueError("meetreeks_id is empty")
    return f"series_{meetreeks_id[:chars]}"


def run_folder_name(when: dt.date | None = None) -> str:
    """``run_yy_mm_dd`` for the given day (today by default)."""
    return f"run_{(when or dt.date.today()):%y_%m_%d}"


def run_dir(base: str, when: dt.date | None = None, name: str | None = None) -> str:
    """The dated run folder under the volume base.

    ``name`` overrides the date entirely -- useful when two runs in one day must
    not share a folder, since artefact filenames would otherwise collide.
    """
    return f"{str(base).rstrip('/')}/{name or run_folder_name(when)}"


def artefact_dir(run_directory: str, kind: str) -> str:
    """One of the three artefact folders inside a run folder."""
    if kind not in ARTEFACT_DIRS:
        raise ValueError(f"Unknown artefact kind {kind!r}; use one of {ARTEFACT_DIRS}")
    return f"{str(run_directory).rstrip('/')}/{kind}"


# --- stage 2: extremes artefacts -------------------------------------------
#
# These live inside the same dated run folder as the models they were derived
# from, so one folder holds a whole run:
#
#   <volume>/run_26_10_15/
#       pas_files/            stage 1
#       results_files/        stage 1
#       diagnostics_files/    stage 1
#       csv/                  <series>_return_periods.csv      <- the deliverable
#       pooled_extremes/      <series>_{dry,wet}.csv
#       used_extremes/        used_extremes_<series>.csv
#       return_plots_fitted/  <series>_return_periods.png
#       qq_plots/             <series>_qq_{min,max}.png
#       confidence_plots/     <series>_return_level_{min,max}.png
#       evt_selection.csv
#       tail_parameters.csv
#       <run>_extremes_summary.csv

RETURN_CSV_DIR = "csv"
POOLED_EXTREMES_DIR = "pooled_extremes"
USED_EXTREMES_DIR = "used_extremes"
RETURN_PLOTS_DIR = "return_plots_fitted"
QQ_PLOTS_DIR = "qq_plots"
CONFIDENCE_PLOTS_DIR = "confidence_plots"

EXTREMES_DIRS = (
    RETURN_CSV_DIR,
    POOLED_EXTREMES_DIR,
    USED_EXTREMES_DIR,
    RETURN_PLOTS_DIR,
    QQ_PLOTS_DIR,
    CONFIDENCE_PLOTS_DIR,
)

# Run-level roll-ups, written at the root of the run folder.
EVT_SELECTION_FILE = "evt_selection.csv"
TAIL_PARAMETERS_FILE = "tail_parameters.csv"


def extremes_dir(run_directory: str, kind: str) -> str:
    """One of the stage-2 artefact folders inside a run folder."""
    if kind not in EXTREMES_DIRS:
        raise ValueError(f"Unknown extremes folder {kind!r}; use one of {EXTREMES_DIRS}")
    return f"{str(run_directory).rstrip('/')}/{kind}"


def extremes_summary_file(run_directory: str) -> str:
    """``<run>_extremes_summary.csv`` at the root of the run folder."""
    run_directory = str(run_directory).rstrip("/")
    run_name = run_directory.rsplit("/", 1)[-1]
    return f"{run_directory}/{run_name}_extremes_summary.csv"

# Provenance for the bias correction applied to the IMPREX forcing. It lives
# beside the member files and is copied into each run folder, so the numbers
# never travel without the record of how the forcing was corrected.
BIAS_LOG_FILE = "bias_correction_run_log.json"
