"""Finding the head CSVs to train on (spec section 7, step 1).

One row per series: ``(survey, file_path, series_name)``. Plain filesystem paths,
because Unity Catalog Volumes mount as ordinary directories
(``/Volumes/<catalog>/<schema>/<volume>/...``) -- no Spark needed to list them.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from tarso_pipeline.naming import sanitize_name

# Metadata that sits beside the series files and is not a series itself.
EXCLUDED_NAMES = frozenset({"object_data.csv"})

DISCOVERY_COLUMNS = ("survey", "file_path", "series_name")


def is_series_file(path: Path) -> bool:
    """A .csv that is a head series rather than metadata."""
    return path.suffix.lower() == ".csv" and path.name.lower() not in EXCLUDED_NAMES


def discover_series(
    input_root: str | Path,
    surveys: list[str] | None = None,
) -> pd.DataFrame:
    """List head CSVs under ``input_root``, one row per series.

    ``input_root`` holds one folder per survey. ``surveys`` limits it to named
    folders; None takes every folder. ``series_name`` is the sanitised file stem,
    which is the Pastas model name and the identifier everywhere downstream --
    source files are never renamed (spec section 2.3).
    """
    root = Path(input_root)
    if not root.is_dir():
        raise NotADirectoryError(f"Input root does not exist or is not a directory: {root}")

    if surveys:
        folders = [root / survey for survey in surveys]
        missing = [str(f) for f in folders if not f.is_dir()]
        if missing:
            raise NotADirectoryError(f"Survey folder(s) not found: {missing}")
    else:
        folders = sorted(p for p in root.iterdir() if p.is_dir())

    rows = []
    for folder in folders:
        for path in sorted(folder.iterdir()):
            if path.is_file() and is_series_file(path):
                rows.append(
                    {
                        "survey": folder.name,
                        "file_path": str(path),
                        "series_name": sanitize_name(path.name),
                    }
                )

    return pd.DataFrame(rows, columns=list(DISCOVERY_COLUMNS))
