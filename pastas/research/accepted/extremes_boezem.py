"""Return periods with the boezem held at its mean vs driven by the synthetic boezem.

    python research/accepted/extremes_boezem.py local_runs/research/accepted_bz2 \
        local_runs/research/boezem_emulator/boezem_synthetic_imprex.csv picks_bz1_baro.csv

For every picked model with a ``boezem`` stress model, the 16 IMPREX members are simulated
twice through ``extremes_run.run_model``: ``held`` (the boezem at its calibration mean, as
``simulate_member`` does by default) and ``synthetic`` (the emulated boezem of that member).
The synthetic series is put on the model's own reference: the model was fitted on
``boezem - c`` with ``c`` its mean over that series' calibration days, so ``c`` is read back
from the stored stress and subtracted from the emulated level.
"""

from __future__ import annotations

import sys
import warnings
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import pandas as pd

PASTAS = Path(__file__).resolve().parents[2]
for path in (PASTAS, PASTAS / "src"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from research.accepted import stresses  # noqa: E402

KEYS = ["dr_emp_T10", "dr_emp_T100", "wet_emp_T10", "wet_emp_T100"]


def reference(model, observed: pd.Series) -> float:
    """The calibration mean ``c`` the boezem stress was shifted by (observed - stored)."""
    stored = model.stressmodels["boezem"].stress[0].series_original
    both = pd.concat([observed, stored], axis=1, keys=["obs", "stored"]).dropna()
    return float((both["obs"] - both["stored"]).median())


def one(row: dict, synthetic_path: str, out: str) -> list[dict]:
    warnings.filterwarnings("ignore")
    import pastas as ps

    from research.accepted.report import _members
    from tarso_pipeline.extremes_run import run_model

    members, forcing = _members()
    synthetic = pd.read_csv(synthetic_path, parse_dates=["date"]).set_index(["member", "date"])["boezem"]
    c = reference(ps.io.load(row["model_path"]), stresses.boezem_level("beemster"))
    with_boezem = {k: (p, e, {"boezem": synthetic.loc[k] - c}) for k, (p, e) in forcing.items()}
    rows = []
    for mode, f in (("held", forcing), ("synthetic", with_boezem)):
        result = run_model(row["model_path"], row["meetreeks_id"], row["series_name"], members,
                           run_directory=str(Path(out) / "extremes_boezem" / mode), run_id=mode, units="m",
                           make_plots=False, forcing=f, model_type=row["model_type"])
        summary = dict(result.summary)
        summary.update({"mode": mode, "reference_c": c})
        rows.append(summary)
    return rows


def main() -> None:
    folder, synthetic_path, picks_name = Path(sys.argv[1]), sys.argv[2], sys.argv[3]
    picks = pd.read_csv(folder / picks_name)
    picks = picks[picks["model_type"].str.contains("bz")]
    print(f"{len(picks)} models with a boezem stress", flush=True)
    rows = picks[["meetreeks_id", "series_name", "model_type", "model_path"]].to_dict("records")
    with ProcessPoolExecutor(7) as pool:
        results = list(pool.map(one, rows, [synthetic_path] * len(rows), [str(folder)] * len(rows)))
    table = pd.DataFrame([r for rs in results for r in rs])
    table.to_csv(folder / "extremes_boezem.csv", index=False)
    wide = table.pivot(index="meetreeks_id", columns="mode", values=KEYS)
    diff = pd.DataFrame({k: (wide[(k, "synthetic")] - wide[(k, "held")]) * 100 for k in KEYS})
    diff.to_csv(folder / "extremes_boezem_diff_cm.csv")
    print("synthetic minus held, cm:")
    print(diff.describe(percentiles=[0.1, 0.5, 0.9]).round(2).to_string())
    print(table.groupby("mode")["status"].value_counts().to_string())


if __name__ == "__main__":
    main()
