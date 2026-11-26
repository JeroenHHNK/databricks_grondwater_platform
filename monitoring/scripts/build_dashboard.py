"""Write databricks_dashboard/Grondwater_Monitoring.lvdash.json from monitoring_lib.dashboard.

    python monitoring/scripts/build_dashboard.py [--catalog waterkeringbeheer_prd] [--schema jvanruitenbeek]

Refuses to write when a widget points at a dataset or column that does not exist.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "monitoring" / "src"))

from monitoring_lib.dashboard import definition  # noqa: E402
from monitoring_lib.lvdash import dashboard, problems  # noqa: E402

TARGET = ROOT / "databricks_dashboard" / "Grondwater_Monitoring.lvdash.json"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", default="waterkeringbeheer_prd")
    parser.add_argument("--schema", default="jvanruitenbeek")
    parser.add_argument("--out", type=Path, default=TARGET)
    args = parser.parse_args()
    datasets, pages = definition(args.catalog, args.schema)
    found = problems(datasets, pages)
    if found:
        print("\n".join(found))
        return 1
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(dashboard(datasets, pages), indent=2, ensure_ascii=False) + "\n",
                        encoding="utf-8")
    widgets = sum(len(p.widgets) for p in pages)
    print(f"wrote {args.out} ({len(datasets)} datasets, {len(pages)} pages, {widgets} widgets)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
