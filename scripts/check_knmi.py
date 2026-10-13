"""Run the KNMI ingestion locally, without Databricks, to check the output.

**Not the Databricks entry point.** On a cluster run the notebook,
``notebooks/ingestion/knmi_forcing.py`` -- that is the one that creates the Delta
table and MERGEs into it. This script writes a local CSV and never touches Delta,
so running it on Databricks does the wrong work even when it does not fail.

The notebook needs a cluster: ``dbutils``, ``spark`` and ``display`` are cluster
globals. Everything the notebook actually *decides* -- which days to fetch, how to
parse them, what the annual totals are, what the chart looks like -- is plain
pandas, and this script runs exactly those functions. The only substitution is the
store: a local CSV instead of a Delta table.

    python scripts/check_knmi.py                  # incremental, into .local/
    python scripts/check_knmi.py --reset          # start the backlog over
    python scripts/check_knmi.py --station 235    # De Kooy instead of Berkhout

Run it twice: the second run should report an empty window and make no request.
That is the behaviour the Delta version relies on, so it is worth seeing directly.
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path

import pandas as pd

def _find_repo_root() -> Path:
    """Locate the repo root without assuming how this file was started.

    ``__file__`` is undefined when the file is ``exec``'d rather than run as a
    script -- which is exactly what Databricks does if you open this .py in the
    workspace and press Run, and what ``exec(open(...).read())`` does anywhere.
    Fall back to walking up from the working directory for the pyproject.
    """
    try:
        return Path(__file__).resolve().parent.parent
    except NameError:
        pass
    for candidate in [Path.cwd(), *Path.cwd().parents]:
        if (candidate / "pyproject.toml").is_file():
            return candidate
    raise RuntimeError(
        "Cannot locate the repo root: no __file__ and no pyproject.toml above "
        f"{Path.cwd()}. Run this from a terminal:  python scripts/check_knmi.py"
    )


REPO_ROOT = _find_repo_root()
sys.path.insert(0, str(REPO_ROOT / "src"))

from tarso_pipeline.forcing_plot import (  # noqa: E402
    annual_totals,
    plot_annual_totals,
    use_headless_backend,
)
from tarso_pipeline.knmi import DEFAULT_STATION, fetch_daily, plan_window  # noqa: E402

KEY = ["station", "date"]


def load_store(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame(columns=["station", "date", "precipitation_mm", "makkink_mm"])
    stored = pd.read_csv(path, parse_dates=["date"])
    return stored


def merge(stored: pd.DataFrame, fetched: pd.DataFrame) -> pd.DataFrame:
    """The CSV stand-in for the notebook's MERGE on (station, date).

    Fetched rows win, so re-fetching a revised day corrects it rather than
    duplicating it -- same semantics as ``WHEN MATCHED THEN UPDATE``.
    """
    if stored.empty:
        combined = fetched
    elif fetched.empty:
        combined = stored
    else:
        combined = pd.concat([stored, fetched], ignore_index=True)
    return (
        combined.sort_values(KEY)
        .drop_duplicates(subset=KEY, keep="last")
        .reset_index(drop=True)
    )


def coverage(df: pd.DataFrame) -> dict:
    span = (df["date"].max() - df["date"].min()).days + 1
    return {
        "first_date": df["date"].min().date(),
        "last_date": df["date"].max().date(),
        "days_stored": len(df),
        "days_expected": span,
        "gap": len(df) - span,
        "null_precipitation": int(df["precipitation_mm"].isna().sum()),
        "null_makkink": int(df["makkink_mm"].isna().sum()),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--station", type=int, default=DEFAULT_STATION)
    parser.add_argument("--backlog-start", default="2020-01-01")
    parser.add_argument("--lookback-days", type=int, default=2)
    parser.add_argument("--out-dir", default=str(REPO_ROOT / ".local"))
    parser.add_argument("--reset", action="store_true", help="discard the local store first")
    args = parser.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    store_path = out_dir / f"knmi_daily_forcing_{args.station}.csv"
    chart_path = out_dir / f"knmi_annual_totals_{args.station}.png"

    if args.reset and store_path.exists():
        store_path.unlink()
        print(f"removed {store_path}")

    stored = load_store(store_path)
    last_stored = None if stored.empty else stored["date"].max().date()

    window = plan_window(
        last_stored=last_stored,
        today=dt.date.today(),
        backlog_start=dt.date.fromisoformat(args.backlog_start),
        lookback_days=args.lookback_days,
    )

    if last_stored is None:
        print(f"store empty -> fetching backlog {window.start} -> {window.end}")
    elif window.is_empty:
        print(f"already current through {last_stored}; no request will be sent")
    else:
        print(f"stored through {last_stored} -> fetching {window.start} -> {window.end}")

    fetched = fetch_daily(window, station=args.station)
    print(f"{len(fetched)} rows from KNMI")

    merged = merge(stored, fetched)
    merged.to_csv(store_path, index=False)
    print(f"store now holds {len(merged)} rows -> {store_path}")

    if merged.empty:
        print("nothing stored yet; nothing to check")
        return 1

    print("\ncoverage")
    for key, value in coverage(merged).items():
        print(f"  {key:<20} {value}")
    gap = coverage(merged)["gap"]
    print("  no missing days" if gap == 0 else f"  WARNING: {abs(gap)} day(s) missing")

    totals = annual_totals(merged)
    print("\nannual totals (mm)")
    print(totals.to_string())

    use_headless_backend()
    fig = plot_annual_totals(totals, args.station)
    fig.savefig(chart_path, bbox_inches="tight", facecolor="#fcfcfb")
    print(f"\nchart -> {chart_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
