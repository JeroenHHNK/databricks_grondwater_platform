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
    """Path of the committed forcing file for one KNMI station.

    One file per station, both variables in it: ``load_forcing()`` selects the
    column, so precipitation and evaporation do not need separate files.
    """
    return forcing_dir(root) / f"knmi_forcing_{station}.csv"
