"""Running the extremes stage for one model (handoff sections 4-6).

``run_model`` is the unit of work: load the `.pas`, simulate all members, pool
the extremes, fit, and write every artefact. It **never raises**.

Failures are per (model, member): a member that fails is recorded and the rest
continue, because a model that loses some members still fits on the remainder. A
model that loses all of them gets a status row and no outputs.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from tarso_pipeline.evt import DEFAULT_T_LIST
from tarso_pipeline.evt_plots import confidence_plot, qq_plot, return_period_plot
from tarso_pipeline.evt_report import (
    TAILS,
    VALUE_COLUMN,
    return_period_table,
    selection_rows,
    summary_row,
    tail_parameter_rows,
    tail_tables,
    used_extremes_table,
)
from tarso_pipeline.extremes import annual_extremes, pool_members
from tarso_pipeline.imprex import Member, load_member
from tarso_pipeline.paths import (
    CONFIDENCE_PLOTS_DIR,
    POOLED_EXTREMES_DIR,
    QQ_PLOTS_DIR,
    RETURN_CSV_DIR,
    RETURN_PLOTS_DIR,
    USED_EXTREMES_DIR,
    extremes_dir,
)
from tarso_pipeline.simulate import simulate_member


@dataclass
class ModelResult:
    summary: dict
    selection: list = field(default_factory=list)
    parameters: list = field(default_factory=list)
    return_table: pd.DataFrame | None = None
    paths: dict = field(default_factory=dict)


def _write(frame: pd.DataFrame, directory: str, filename: str, index: bool = True) -> str:
    path = Path(directory) / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(path, index=index)
    return str(path)


def run_model(
    model_path: str,
    series_id: str,
    series_name: str,
    members: list[Member],
    run_directory: str,
    run_id: str,
    units: str = "m",
    warmup_years: int = 5,
    exclude_first: int = 0,
    t_list=DEFAULT_T_LIST,
    replicates: int = 200,
    confidence_replicates: int = 500,
    make_plots: bool = True,
    seed: int = 0,
) -> ModelResult:
    """Simulate, pool, fit and write everything for one model. Never raises."""
    failures: list[tuple[str, str]] = []
    dry_per: dict[str, pd.DataFrame] = {}
    wet_per: dict[str, pd.DataFrame] = {}

    try:
        import pastas as ps
    except Exception as exc:  # pragma: no cover - pastas is a hard dependency here
        return ModelResult(
            summary=summary_row(series_id, series_name, run_id, {}, {}, 0,
                                [("import", str(exc))], "error", exclude_first, t_list)
        )

    for member in members:
        try:
            precipitation, evaporation = load_member(member, units=units)
            # Reload per member: simulate_member mutates the model's stresses.
            model = ps.io.load(str(model_path))
            result = simulate_member(
                model, precipitation, evaporation,
                member=member.member, warmup_years=warmup_years,
            )
            dry, wet = annual_extremes(result.head)
            dry_per[member.member] = dry
            wet_per[member.member] = wet
        except Exception as exc:  # noqa: BLE001 - a lost member is data, not a crash
            failures.append((member.member, f"{type(exc).__name__}: {exc}"))

    pooled = {"dry": pool_members(dry_per), "wet": pool_members(wet_per)}
    n_members = len(dry_per)

    if n_members == 0:
        return ModelResult(
            summary=summary_row(series_id, series_name, run_id, pooled, {}, 0,
                                failures, "error", exclude_first, t_list)
        )

    per_tail: dict[str, dict] = {}
    for tail in TAILS:
        frame = pooled[tail]
        if frame is None or frame.empty:
            continue
        try:
            per_tail[tail] = tail_tables(
                frame[VALUE_COLUMN[tail]].to_numpy(), tail,
                exclude_first=exclude_first, t_list=t_list,
                replicates=replicates, seed=seed,
            )
        except Exception as exc:  # noqa: BLE001
            failures.append((f"{tail}-fit", f"{type(exc).__name__}: {exc}"))

    status = "ok" if not failures else ("partial" if per_tail else "error")
    result = ModelResult(
        summary=summary_row(series_id, series_name, run_id, pooled, per_tail,
                            n_members, failures, status, exclude_first, t_list),
        selection=selection_rows(series_id, series_name, run_id, per_tail),
        parameters=tail_parameter_rows(series_id, series_name, run_id, per_tail),
    )

    # --- artefacts ---------------------------------------------------------
    try:
        for tail in TAILS:
            frame = pooled[tail]
            if frame is not None and not frame.empty:
                result.paths[f"pooled_{tail}"] = _write(
                    frame, extremes_dir(run_directory, POOLED_EXTREMES_DIR),
                    f"{series_name}_{tail}.csv",
                )

        used = used_extremes_table(pooled, exclude_first)
        if not used.empty:
            result.paths["used_extremes"] = _write(
                used, extremes_dir(run_directory, USED_EXTREMES_DIR),
                f"used_extremes_{series_name}.csv", index=False,
            )

        if per_tail:
            table = return_period_table(per_tail, t_list)
            result.return_table = table
            result.paths["return_periods"] = _write(
                table, extremes_dir(run_directory, RETURN_CSV_DIR),
                f"{series_name}_return_periods.csv",
            )

        if make_plots and per_tail:
            result.paths["return_plot"] = return_period_plot(
                per_tail,
                Path(extremes_dir(run_directory, RETURN_PLOTS_DIR))
                / f"{series_name}_return_periods.png",
                f"Return periods - {series_name}",
            )
            result.paths["qq_plot"] = qq_plot(
                per_tail,
                Path(extremes_dir(run_directory, QQ_PLOTS_DIR)) / f"{series_name}_qq.png",
                f"QQ - {series_name}",
            )
            result.paths["confidence_plot"] = confidence_plot(
                per_tail,
                Path(extremes_dir(run_directory, CONFIDENCE_PLOTS_DIR))
                / f"{series_name}_return_level.png",
                f"Return level with 95% CI - {series_name}",
                replicates=confidence_replicates, seed=seed,
                exclude_first=exclude_first,
            )
    except Exception as exc:  # noqa: BLE001 - artefacts must not lose the fit
        result.summary["failure_detail"] = "; ".join(
            filter(None, [result.summary.get("failure_detail"), f"artefacts: {exc}"])
        )

    for key in ("return_periods", "return_plot", "qq_plot", "confidence_plot",
                "used_extremes"):
        result.summary[f"{key}_path"] = result.paths.get(key)
    return result


def discover_models(run_directory: str) -> pd.DataFrame:
    """Every ``.pas`` in a run folder's ``pas_files``, as (series_name, path)."""
    from tarso_pipeline.paths import PAS_DIR, artefact_dir

    directory = Path(artefact_dir(run_directory, PAS_DIR))
    if not directory.is_dir():
        raise NotADirectoryError(
            f"No pas_files in {run_directory}. Has the fit stage run for this run folder?"
        )
    rows = []
    for path in sorted(directory.glob("*.pas")):
        stem = path.stem
        # series_<hash>_<model_type> -> strip the trailing model type
        series_name = stem.rsplit("_", 1)[0] if "_" in stem else stem
        rows.append(
            {"series_name": series_name, "model_path": str(path), "model_file": path.name}
        )
    return pd.DataFrame(rows, columns=["series_name", "model_path", "model_file"])
