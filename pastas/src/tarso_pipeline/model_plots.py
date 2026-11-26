"""Drawing the figures of the models a series uses, and only those.

Every series is fitted with several model types, but only one of them is used (the pick
of ``tarso_pipeline.selection``, or a person's). Drawing ``ml.plots.results()``,
diagnostics and the extremes figures for all of them fills the run folder with PNGs
nobody looks at (five models x five kinds per series). So the fit and extremes stages
draw nothing by default (``make_plots="in_use"``) and, once the pick is known,
:func:`plot_models` draws them for the models in use from what the run folder already
holds: the ``.pas`` file and the pooled extremes. Nothing is refitted that matters:
the extremes are refitted from the pooled years, which is the same deterministic fit.

A model picked later in the app has no figures yet; ``plot_models`` (or the notebook
``analysis/plot_models_in_use.py``) draws the missing ones, ``only_missing=True``.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from tarso_pipeline.evt import DEFAULT_KEPT_GRID, DEFAULT_T_LIST
from tarso_pipeline.evt_report import TAILS
from tarso_pipeline.paths import (
    DIAGNOSTICS_DIR,
    PAS_DIR,
    POOLED_EXTREMES_DIR,
    RESULTS_DIR,
    RETURN_PLOTS_DIR,
    artefact_dir,
    extremes_dir,
)

PLOT_MODES = ("in_use", "all", "none")


def check_mode(mode: str) -> str:
    if mode not in PLOT_MODES:
        raise ValueError(f"plot mode must be one of {PLOT_MODES}, got {mode!r}")
    return mode


def _stem(series_name: str, model_type: str) -> str:
    return f"{series_name}_{model_type}"


def read_pooled_frames(run_directory: str, series_name: str, model_type: str) -> dict[str, pd.DataFrame]:
    """The pooled annual extremes run_extremes wrote for one model, per tail (as written)."""
    folder = Path(extremes_dir(run_directory, POOLED_EXTREMES_DIR))
    pooled = {}
    for tail in TAILS:
        stems = [_stem(series_name, model_type)] + ([series_name] if model_type == "tarso" else [])
        for stem in stems:
            path = folder / f"{stem}_{tail}.csv"
            if path.is_file():
                pooled[tail] = pd.read_csv(path, index_col=0)
                break
    return pooled


def has_plots(run_directory: str, series_name: str, model_type: str) -> bool:
    stem = _stem(series_name, model_type)
    return ((Path(artefact_dir(run_directory, RESULTS_DIR)) / f"{stem}_results.png").is_file()
            and (Path(extremes_dir(run_directory, RETURN_PLOTS_DIR)) / f"{stem}_return_periods.png").is_file())


def fit_plots(run_directory: str, series_name: str, model_type: str) -> dict[str, str | None]:
    """``ml.plots.results()`` and ``.diagnostics()`` of one model, from its ``.pas``."""
    import pastas as ps

    from tarso_pipeline.fit import _save_plot

    stem = _stem(series_name, model_type)
    model = ps.io.load(str(Path(artefact_dir(run_directory, PAS_DIR)) / f"{stem}.pas"))
    return {
        "results": _save_plot(model, "results", Path(artefact_dir(run_directory, RESULTS_DIR)) / f"{stem}_results.png"),
        "diagnostics": _save_plot(model, "diagnostics",
                                  Path(artefact_dir(run_directory, DIAGNOSTICS_DIR)) / f"{stem}_diagnostics.png"),
    }


def extremes_plots(run_directory: str, meetreeks_id: str, series_name: str, model_type: str,
                   exclude_first: int = 450, t_list=DEFAULT_T_LIST, kept_grid=DEFAULT_KEPT_GRID) -> dict:
    """The return-period, threshold and QQ figures of one model, from its pooled extremes."""
    from tarso_pipeline.extremes_run import fit_pooled

    pooled = read_pooled_frames(run_directory, series_name, model_type)
    if not pooled:
        return {}
    first = next(iter(pooled.values()))
    n_members = int(pd.concat(pooled.values())["member"].nunique()) if "member" in first else 16
    result = fit_pooled(pooled, meetreeks_id, series_name, run_directory, "plots", n_members,
                        exclude_first=exclude_first, t_list=t_list, kept_grid=kept_grid, make_plots=True,
                        model_type=model_type)
    return {k: v for k, v in result.paths.items() if k.endswith("_plot")}


def plot_models(run_directory: str, models: pd.DataFrame, only_missing: bool = False,
                exclude_first: int = 450, t_list=DEFAULT_T_LIST, kept_grid=DEFAULT_KEPT_GRID) -> pd.DataFrame:
    """Draw every figure of the ``models`` (meetreeks_id, series_name, model_type). Never raises.

    Returns one row per model: what was drawn, or why not.
    """
    rows = []
    for item in models.drop_duplicates(["series_name", "model_type"]).itertuples():
        row = {"series_name": item.series_name, "model_type": item.model_type, "status": "ok", "detail": ""}
        if only_missing and has_plots(run_directory, item.series_name, item.model_type):
            rows.append({**row, "status": "skipped", "detail": "already drawn"})
            continue
        try:
            drawn = {**fit_plots(run_directory, item.series_name, item.model_type),
                     **extremes_plots(run_directory, item.meetreeks_id, item.series_name, item.model_type,
                                      exclude_first, t_list, kept_grid)}
            missing = [k for k, v in drawn.items() if not v]
            row["detail"] = f"{sum(bool(v) for v in drawn.values())} figure(s)" + (
                f"; not drawn: {', '.join(missing)}" if missing else "")
        except Exception as exc:  # noqa: BLE001 - a missing figure must not stop the rest
            row.update(status="error", detail=f"{type(exc).__name__}: {exc}"[:300])
        rows.append(row)
    return pd.DataFrame(rows, columns=["series_name", "model_type", "status", "detail"])


def prune_plots(run_directory: str, keep_stems: set[str], dry_run: bool = False) -> list[str]:
    """Remove the figures in one run folder whose model is not in ``keep_stems``.

    Only PNGs of ``results.PLOT_KINDS`` are touched; the ``.pas`` files and the CSVs
    (pooled extremes, return periods) stay, so a figure can always be drawn again.
    Returns the removed (or, with ``dry_run``, the removable) paths.
    """
    from tarso_pipeline.results import PLOT_KINDS

    removed = []
    for folder, suffix in PLOT_KINDS.values():
        for path in Path(run_directory).glob(f"**/{folder}/*{suffix}"):
            if path.name[: -len(suffix)] not in keep_stems:
                removed.append(str(path))
                if not dry_run:
                    path.unlink()
    return removed


def stems_in_use(in_use: pd.DataFrame, series_names: pd.DataFrame) -> set[str]:
    """The plot file stems of the models in use, for ``results.sync_plots(stems=...)``.

    ``in_use``: meetreeks_id, model_type (``selection.model_in_use``); ``series_names``:
    meetreeks_id, series_name. Tarso also gets its old-style bare stem (runs from before
    the plots carried their model type).
    """
    merged = in_use.merge(series_names.drop_duplicates("meetreeks_id"), on="meetreeks_id", how="inner")
    stems = {_stem(s, m) for s, m in zip(merged["series_name"], merged["model_type"])}
    stems |= set(merged.loc[merged["model_type"] == "tarso", "series_name"])
    return stems
