"""Run every test suite of the repo, each from its own folder, in the root uv environment.

    uv run python scripts/run_tests.py            # fast: skips the real Pastas fits
    uv run python scripts/run_tests.py --slow     # also the real fits

Each project keeps its own pytest settings (``pythonpath``, markers) in its own
``pyproject.toml``, so the suites cannot share one pytest run; this runs them one after
the other and fails if any fails. The Pastas suite gets ``pastas`` on the fly
(``uv run --with``): it is not in the root lock, because the Databricks App installs from
that lock and does not need it.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PASTAS_PIN = "pastas==1.12.*"  # the version the Databricks notebooks %pip install


def suites(slow: bool) -> list[tuple[str, Path, list[str]]]:
    pytest = ["python", "-m", "pytest", "-q"]
    uv = shutil.which("uv")
    if uv is None:
        raise SystemExit("uv not found: install it (https://docs.astral.sh/uv/) or run the suites by hand")
    run = [uv, "run", "--project", str(ROOT)]
    return [
        ("central app", ROOT, [*run, *pytest, "tests"]),
        ("data validation", ROOT / "data_validation", [*run, *pytest, "tests"]),
        ("monitoring", ROOT / "monitoring", [*run, *pytest]),
        ("boxplots", ROOT / "boxplots", [*run, *pytest, "tests"]),
        ("pastas", ROOT / "pastas", [*run, "--with", PASTAS_PIN, *pytest, *([] if slow else ["-m", "not slow"])]),
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--slow", action="store_true", help="also run the real Pastas fits")
    args = parser.parse_args()
    failed = []
    for name, folder, command in suites(args.slow):
        print(f"\n=== {name} ({folder.relative_to(ROOT).as_posix() or '.'})", flush=True)
        if subprocess.run(command, cwd=folder).returncode != 0:
            failed.append(name)
    print("\n" + ("all suites passed" if not failed else f"FAILED: {', '.join(failed)}"))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
