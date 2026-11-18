"""Running the extremes stage for one model (handoff sections 4-6).

``run_model`` is the unit of work: load the `.pas`, simulate all members, pool
the extremes, fit, and write every artefact. It **never raises**. It is two steps,
usable on their own: :func:`simulate_pooled` (members -> pooled annual extremes) and
:func:`fit_pooled` (pooled extremes -> levels, tables, figures), so pooled extremes
written by an earlier run can be refitted without simulating again.

No family is chosen: the GPD and exponential are fitted above the threshold for a
range of thresholds, the GEV on all years for comparison, and a person picks per
tail from the plots. The bootstrap (``confidence_plots``) runs separately:
``tarso_pipeline.uncertainty`` and ``notebooks/analysis/explore_shape_and_uncertainty.py``.

Failures are per (model, member): a member that fails is recorded and the rest
continue, because a model that loses some members still fits on the remainder. A
model that loses all of them gets a status row and no outputs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from tarso_pipeline.evt import DEFAULT_KEPT_GRID, DEFAULT_T_LIST
from tarso_pipeline.evt_plots import qq_plot, return_period_plot, threshold_plot
from tarso_pipeline.evt_report import (
    TAILS,
    VALUE_COLUMN,
    return_level_rows,
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
    POOLED_EXTREMES_DIR,
    QQ_PLOTS_DIR,
    RETURN_CSV_DIR,
    RETURN_PLOTS_DIR,
    THRESHOLD_PLOTS_DIR,
    USED_EXTREMES_DIR,
    extremes_dir,
)
from tarso_pipeline.simulate import simulate_member


@dataclass
class ModelResult:
    summary: dict
    selection: list = field(default_factory=list)
    parameters: list = field(default_factory=list)
    levels: list = field(default_factory=list)
    return_table: pd.DataFrame | None = None
    paths: dict = field(default_factory=dict)
    per_tail: dict = field(default_factory=dict)


def _write(frame: pd.DataFrame, directory: str, filename: str, index: bool = True) -> str:
    path = Path(directory) / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(path, index=index)
    return str(path)


def simulate_pooled(
    model_path: str,
    members: list[Member],
    units: str = "m",
    warmup_years: int = 5,
    forcing: dict | None = None,
) -> tuple[dict, int, list]:
    """Simulate every member and pool the annual extremes: ``(pooled, n_members, failures)``.

    ``forcing`` may map a member name to its loaded ``(precipitation, evaporation)``,
    so a run over many models reads the ensemble once.
    """
    import pastas as ps

    failures: list[tuple[str, str]] = []
    dry_per: dict[str, pd.DataFrame] = {}
    wet_per: dict[str, pd.DataFrame] = {}
    for member in members:
        try:
            if forcing is not None and member.member in forcing:
                precipitation, evaporation = forcing[member.member]
            else:
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
    return pooled, len(dry_per), failures


def fit_pooled(
    pooled: dict,
    meetreeks_id: str,
    series_name: str,
    run_directory: str | None,
    run_id: str,
    n_members: int,
    failures: list | None = None,
    exclude_first: int = 450,
    t_list=DEFAULT_T_LIST,
    kept_grid=DEFAULT_KEPT_GRID,
    make_plots: bool = True,
    model_type: str = "tarso",
) -> ModelResult:
    """Fit both tails of pooled extremes and write the artefacts (when ``run_directory``).

    Every artefact is named ``<series_name>_<model_type>_...``: one series has a model
    per model type, and they share the folders.
    """
    failures = list(failures or [])
    stem = model_stem(series_name, model_type)
    if n_members == 0:
        return ModelResult(
            summary=summary_row(meetreeks_id, series_name, run_id, pooled, {}, 0,
                                failures, "error", exclude_first, t_list, model_type=model_type)
        )

    per_tail: dict[str, dict] = {}
    for tail in TAILS:
        frame = pooled.get(tail)
        if frame is None or frame.empty:
            continue
        try:
            per_tail[tail] = tail_tables(
                frame[VALUE_COLUMN[tail]].to_numpy(), tail,
                exclude_first=min(exclude_first, len(frame) - 1), t_list=t_list, kept_grid=kept_grid,
            )
        except Exception as exc:  # noqa: BLE001
            failures.append((f"{tail}-fit", f"{type(exc).__name__}: {exc}"))

    # The threshold the fits used (snapped to the scan), for the tables below.
    exclude_first = next((d["exclude_first"] for d in per_tail.values()), exclude_first)
    status = "ok" if not failures else ("partial" if per_tail else "error")
    result = ModelResult(
        summary=summary_row(meetreeks_id, series_name, run_id, pooled, per_tail,
                            n_members, failures, status, exclude_first, t_list, model_type=model_type),
        selection=selection_rows(meetreeks_id, series_name, run_id, per_tail, model_type=model_type),
        parameters=tail_parameter_rows(meetreeks_id, series_name, run_id, per_tail, model_type=model_type),
        levels=return_level_rows(meetreeks_id, series_name, run_id, per_tail, model_type=model_type),
        per_tail=per_tail,
    )
    if per_tail:
        result.return_table = return_period_table(per_tail, t_list)
    if not run_directory:
        return result

    # --- artefacts ---------------------------------------------------------
    try:
        for tail in TAILS:
            frame = pooled.get(tail)
            if frame is not None and not frame.empty:
                result.paths[f"pooled_{tail}"] = _write(
                    frame, extremes_dir(run_directory, POOLED_EXTREMES_DIR),
                    f"{stem}_{tail}.csv",
                )

        used = used_extremes_table(pooled, exclude_first)
        if not used.empty:
            result.paths["used_extremes"] = _write(
                used, extremes_dir(run_directory, USED_EXTREMES_DIR),
                f"used_extremes_{stem}.csv", index=False,
            )

        if result.return_table is not None:
            result.paths["return_periods"] = _write(
                result.return_table, extremes_dir(run_directory, RETURN_CSV_DIR),
                f"{stem}_return_periods.csv",
            )

        if make_plots and per_tail:
            result.paths["return_plot"] = return_period_plot(
                per_tail,
                Path(extremes_dir(run_directory, RETURN_PLOTS_DIR)) / f"{stem}_return_periods.png",
                f"Return periods - {series_name} ({model_type})",
            )
            result.paths["threshold_plot"] = threshold_plot(
                per_tail,
                Path(extremes_dir(run_directory, THRESHOLD_PLOTS_DIR)) / f"{stem}_threshold.png",
                f"Threshold choice - {series_name} ({model_type})",
            )
            result.paths["qq_plot"] = qq_plot(
                per_tail,
                Path(extremes_dir(run_directory, QQ_PLOTS_DIR)) / f"{stem}_qq.png",
                f"QQ - {series_name} ({model_type})",
            )
    except Exception as exc:  # noqa: BLE001 - artefacts must not lose the fit
        result.summary["failure_detail"] = "; ".join(
            filter(None, [result.summary.get("failure_detail"), f"artefacts: {exc}"])
        )

    for key in ("return_periods", "return_plot", "threshold_plot", "qq_plot", "used_extremes"):
        result.summary[f"{key}_path"] = result.paths.get(key)
    return result


def run_model(
    model_path: str,
    meetreeks_id: str,
    series_name: str,
    members: list[Member],
    run_directory: str,
    run_id: str,
    units: str = "m",
    warmup_years: int = 5,
    exclude_first: int = 450,
    t_list=DEFAULT_T_LIST,
    kept_grid=DEFAULT_KEPT_GRID,
    make_plots: bool = True,
    forcing: dict | None = None,
    model_type: str = "tarso",
) -> ModelResult:
    """Simulate, pool, fit and write everything for one model. Never raises."""
    try:
        pooled, n_members, failures = simulate_pooled(model_path, members, units, warmup_years, forcing)
    except Exception as exc:  # noqa: BLE001 - e.g. pastas missing
        return ModelResult(
            summary=summary_row(meetreeks_id, series_name, run_id, {}, {}, 0,
                                [("simulate", f"{type(exc).__name__}: {exc}")], "error", exclude_first, t_list,
                                model_type=model_type)
        )
    return fit_pooled(pooled, meetreeks_id, series_name, run_directory, run_id, n_members, failures,
                      exclude_first=exclude_first, t_list=t_list, kept_grid=kept_grid, make_plots=make_plots,
                      model_type=model_type)


def model_stem(series_name: str, model_type: str) -> str:
    """``<series_name>_<model_type>``: the stem of every file of one model."""
    return f"{series_name}_{model_type}"


def split_stem(stem: str) -> tuple[str, str]:
    """``series_<hash>_<model_type>`` -> (series_name, model_type).

    The model type is matched against the known ones, longest first, because it may
    itself contain underscores (``linear_gamma``). A stem without one is a file from
    before the artefacts carried their model type, when every model was Tarso.
    """
    from tarso_pipeline.fit import MODEL_TYPES

    for model_type in sorted(MODEL_TYPES, key=len, reverse=True):
        if stem.endswith(f"_{model_type}"):
            return stem[: -len(model_type) - 1], model_type
    return stem, "tarso"


def discover_models(run_directory: str, model_types=None) -> pd.DataFrame:
    """Every ``.pas`` in a run folder's ``pas_files``, as (series_name, model_type, path).

    ``model_types`` keeps only those types (None: all).
    """
    from tarso_pipeline.paths import PAS_DIR, artefact_dir

    directory = Path(artefact_dir(run_directory, PAS_DIR))
    if not directory.is_dir():
        raise NotADirectoryError(
            f"No pas_files in {run_directory}. Has the fit stage run for this run folder?"
        )
    rows = []
    for path in sorted(directory.glob("*.pas")):
        series_name, model_type = split_stem(path.stem)
        if model_types is not None and model_type not in model_types:
            continue
        rows.append({"series_name": series_name, "model_type": model_type, "model_path": str(path),
                     "model_file": path.name})
    return pd.DataFrame(rows, columns=["series_name", "model_type", "model_path", "model_file"])
