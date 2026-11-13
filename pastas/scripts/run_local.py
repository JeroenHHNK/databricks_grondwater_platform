"""Run the Pastas pipeline locally on the validated review files, without Databricks.

    python scripts/run_local.py [--imprex-dir DIR | --pooled-dir DIR] [--limit N] [--export]
                                [--model-types tarso,linear_gamma,...] [--noise none|ar1|arma]
                                [--holdout 0.3] [--workers N]

The same steps and defaults as the Databricks chain, in one process:

1. **validated heads** from ``data_validation/input_output/output_repository/*.csv``: the
   approved hours (``gecontroleerd == 1`` and ``afgekeurd != 1``), ``raw_data + correctie``,
   matched to their ``meetreeks_id`` through ``object_data.csv`` -- the same rule as
   ``03_validated_to_delta``;
2. **fit** every series spanning ``--min-days`` (365) with ``fit_series``, once per model
   type (``--model-types``, default ``tarso_pipeline.fit.DEFAULT_MODEL_TYPES``), on the KNMI
   forcing in ``data/forcing/`` (``fit_pastas_models``);
3. **extremes** (``run_extremes``), either by simulating the IMPREX ensemble
   (``--imprex-dir``: the ``imprex_<location>_ens*_daily_*.csv`` files of the volume's
   ``knmi_forcing`` folder) or by refitting pooled extremes written by an earlier run
   (``--pooled-dir``: a ``pooled_extremes`` folder, e.g. downloaded from the volume), per model, ``--workers``
   at a time; then the **model selection** (``tarso_pipeline.selection``);
4. with ``--export``: write the results to ``results_export/valid_pastas/`` the way
   ``export_results`` does, so the app shows them.

Everything else lands in ``local_runs/valid_pastas/<run>/`` (git-ignored), in the same
layout as a run folder on the volume. Needs the ``pastas`` extra.
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path

import pandas as pd

PASTAS = Path(__file__).resolve().parents[1]
REPO = PASTAS.parent
sys.path.insert(0, str(PASTAS / "src"))

from tarso_pipeline import results  # noqa: E402
from tarso_pipeline.evt import DEFAULT_KEPT_GRID, DEFAULT_T_LIST  # noqa: E402
from tarso_pipeline.evt_report import RETURN_LEVEL_COLUMNS, VALUE_COLUMN  # noqa: E402
from tarso_pipeline.extremes_run import fit_pooled, run_model  # noqa: E402
from tarso_pipeline.fit import DEFAULT_MODEL_TYPES, MODEL_TYPES, fit_series  # noqa: E402
from tarso_pipeline.extremes_run import model_stem  # noqa: E402
from tarso_pipeline.model_plots import plot_models, stems_in_use  # noqa: E402
from tarso_pipeline.selection import model_choice_path, model_in_use, read_model_choice, select_models  # noqa: E402
from tarso_pipeline.forcing import forcing_file, load_forcing_pair  # noqa: E402
from tarso_pipeline.paths import (  # noqa: E402
    EVT_SELECTION_FILE,
    TABLE_PREFIX,
    VALIDATED,
    extremes_summary_file,
    series_name_from_id,
)

REVIEWS = REPO / "data_validation" / "input_output" / "output_repository"
OBJECT_DATA = REPO / "data_validation" / "input_output" / "output_databricks" / "metadata" / "object_data.csv"
LOCAL_RUNS = PASTAS / "local_runs"


def validated_heads(reviews: Path = REVIEWS, object_data: Path = OBJECT_DATA) -> dict[str, pd.DataFrame]:
    """``meetreeks_id -> frame(tijdstempel, waarde)`` of the approved, corrected hours."""
    ids = pd.read_csv(object_data, usecols=["filename", "meetreeks_id"]).drop_duplicates("filename")
    by_file = dict(zip(ids["filename"], ids["meetreeks_id"]))
    out = {}
    for path in sorted(reviews.glob("*.csv")):
        meetreeks_id = by_file.get(path.stem)
        if meetreeks_id is None:
            print(f"  skip {path.name}: not in object_data.csv")
            continue
        frame = pd.read_csv(path, encoding="utf-8-sig")
        for column in ("gecontroleerd", "afgekeurd"):
            frame[column] = pd.to_numeric(frame[column], errors="coerce").fillna(0).astype(int)
        frame["correctie"] = pd.to_numeric(frame.get("correctie", 0.0), errors="coerce").fillna(0.0)
        frame = frame[(frame["gecontroleerd"] == 1) & (frame["afgekeurd"] != 1)]
        # The review store keys on UTC; the source's tijdstempel is the Amsterdam wall clock.
        stamps = pd.to_datetime(frame["timestamp"], utc=True).dt.tz_convert("Europe/Amsterdam").dt.tz_localize(None)
        out[meetreeks_id] = pd.DataFrame({"tijdstempel": stamps.to_numpy(),
                                          "waarde": (frame["raw_data"] + frame["correctie"]).to_numpy()})
    return out


def fit_all(heads: dict, run_directory: Path, run_id: str, args) -> tuple[pd.DataFrame, list, list]:
    precipitation, evaporation = load_forcing_pair(forcing_file(str(PASTAS / "data" / "forcing"), args.station),
                                                   units="m")
    rows, check_rows, simulations = [], [], []
    for meetreeks_id, raw in heads.items():
        series_name = series_name_from_id(meetreeks_id)
        for model_type in args.model_types:
            row = fit_series(
                meetreeks_id=meetreeks_id, series_name=series_name, raw=raw,
                precipitation=precipitation, evaporation=evaporation, run_id=run_id, model_type=model_type,
                run_directory=str(run_directory), min_days=args.min_days, min_obs=100, warmup_days=3650,
                forcing_station=str(args.station), forcing_units="m", save_plot=args.plots == "all",
                noise=args.noise, holdout=args.holdout,
                check_rows=check_rows, simulation_frames=simulations,
            )
            rows.append(row)
            holdout = "" if row.get("r2_holdout") is None else f"  holdout={row['r2_holdout']:.3f}"
            status = (f"R2={row['r2']:.3f}{holdout}  AIC={row['aic']:.1f}  k={row['n_param']}" if row["success"]
                      else f"skipped: {row['error_message']}")
            print(f"  {series_name}  {model_type:16} {status}", flush=True)
    return pd.DataFrame(rows), check_rows, simulations


_ENSEMBLE: dict = {}


def _extremes_one(item: dict, run_directory: str, run_id: str, args_dict: dict):
    """One model's extremes, in a worker process: (summary, selection, levels)."""
    import warnings

    warnings.filterwarnings("ignore")
    import pastas as ps

    ps.set_log_level("ERROR")
    if args_dict["imprex_dir"]:
        from tarso_pipeline.imprex import discover_members, load_member

        key = (str(args_dict["imprex_dir"]), args_dict["location"])
        if key not in _ENSEMBLE:  # once per worker process, not once per model
            members = discover_members(*key)
            _ENSEMBLE[key] = (members, {m.member: load_member(m, units="m") for m in members})
        members, forcing = _ENSEMBLE[key]
        result = run_model(item["model_path"], item["meetreeks_id"], item["series_name"], members,
                           run_directory, run_id, exclude_first=args_dict["exclude_first"],
                           t_list=DEFAULT_T_LIST, kept_grid=DEFAULT_KEPT_GRID, forcing=forcing,
                           model_type=item["model_type"], make_plots=args_dict["plots"] == "all")
    else:
        pooled = {}
        for tail in ("dry", "wet"):
            for stem in (model_stem(item["series_name"], item["model_type"]), item["series_name"]):
                path = Path(args_dict["pooled_dir"]) / f"{stem}_{tail}.csv"
                if path.is_file():
                    pooled[tail] = pd.read_csv(path, index_col=0)
                    break
        if not pooled:
            return None
        first = next(iter(pooled.values()))
        n_members = int(pd.concat(pooled.values())["member"].nunique()) if "member" in first else 16
        result = fit_pooled(pooled, item["meetreeks_id"], item["series_name"], run_directory, run_id,
                            n_members, exclude_first=args_dict["exclude_first"], model_type=item["model_type"],
                            make_plots=args_dict["plots"] == "all")
    return result.summary, result.selection, result.levels


def extremes_all(fits: pd.DataFrame, run_directory: Path, run_id: str, args) -> dict[str, pd.DataFrame]:
    from concurrent.futures import ProcessPoolExecutor, as_completed

    selections, summaries, levels = [], [], []
    ok = fits[fits["success"].astype(bool)]
    items = ok[["model_path", "meetreeks_id", "series_name", "model_type"]].to_dict("records")
    args_dict = {"imprex_dir": args.imprex_dir, "pooled_dir": args.pooled_dir, "location": args.location,
                 "exclude_first": args.exclude_first, "plots": args.plots}
    print(f"  {len(items)} model(s), {args.workers} at a time")
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(_extremes_one, item, str(run_directory), run_id, args_dict): item for item in items}
        for future in as_completed(futures):
            item = futures[future]
            try:
                out = future.result()
            except Exception as exc:  # noqa: BLE001
                print(f"  {item['series_name']}  {item['model_type']}: FAILED {exc}")
                continue
            if out is None:
                print(f"  {item['series_name']}  {item['model_type']}: no pooled extremes")
                continue
            summary, selection, level_rows = out
            selections.extend(selection)
            summaries.append(summary)
            levels.extend(level_rows)
            print(f"  {item['series_name']}  {item['model_type']:16} {summary['status']}  emp T100 "
                  f"dry={summary.get('dr_emp_T100')} wet={summary.get('wet_emp_T100')}", flush=True)
    return {"evt_selection": pd.DataFrame(selections), "extremes_summary": pd.DataFrame(summaries),
            "return_levels": pd.DataFrame(levels, columns=RETURN_LEVEL_COLUMNS)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--imprex-dir", type=Path, help="folder with the IMPREX member CSVs")
    source.add_argument("--pooled-dir", type=Path, help="a pooled_extremes folder to refit")
    parser.add_argument("--location", default="berkhout")
    parser.add_argument("--station", default="249")
    parser.add_argument("--min-days", type=int, default=365)
    parser.add_argument("--exclude-first", type=int, default=450)
    parser.add_argument("--limit", type=int, default=0, help="first N series only")
    parser.add_argument("--run-folder", default=None)
    parser.add_argument("--export", action="store_true", help="write to results_export/valid_pastas/")
    parser.add_argument("--model-types", default=",".join(DEFAULT_MODEL_TYPES),
                        help=f"comma-separated, of {', '.join(MODEL_TYPES)}")
    parser.add_argument("--noise", default="none", choices=["none", "ar1", "arma"])
    parser.add_argument("--holdout", type=float, default=0.3, help="share held out for r2_holdout (0 = off)")
    parser.add_argument("--workers", type=int, default=4, help="extremes runs in parallel")
    parser.add_argument("--plots", default="in_use", choices=["in_use", "all", "none"],
                        help="figures of the models in use only (default), of every model, or none")
    args = parser.parse_args()
    args.model_types = [m.strip() for m in args.model_types.split(",") if m.strip()]
    unknown = [m for m in args.model_types if m not in MODEL_TYPES]
    if unknown:
        parser.error(f"unknown model type(s) {unknown}; use {MODEL_TYPES}")

    run_id = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    base = LOCAL_RUNS / TABLE_PREFIX[VALIDATED]
    run_directory = base / (args.run_folder or f"run_local_{dt.date.today():%y_%m_%d}")
    run_directory.mkdir(parents=True, exist_ok=True)
    print(f"run {run_id} -> {run_directory}")

    heads = validated_heads()
    if args.limit:
        heads = dict(list(heads.items())[: args.limit])
    print(f"{len(heads)} validated series")
    fits, check_rows, simulations = fit_all(heads, run_directory, run_id, args)
    tables = {"fit_metrics": fits, "model_checks": pd.DataFrame(check_rows),
              "simulations": pd.concat(simulations, ignore_index=True) if simulations else pd.DataFrame()}

    if args.imprex_dir or args.pooled_dir:
        print("extremes")
        tables.update(extremes_all(fits, run_directory, run_id, args))
        tables["evt_selection"].to_csv(run_directory / EVT_SELECTION_FILE, index=False)
        tables["extremes_summary"].to_csv(extremes_summary_file(run_directory.as_posix()), index=False)
        tables["return_levels"].to_csv(run_directory / "return_levels.csv", index=False)
    tables["model_selection"] = select_models(fits, tables["model_checks"], tables.get("extremes_summary"), run_id)
    tables["model_selection"].to_csv(run_directory / "model_selection.csv", index=False)
    chosen = tables["model_selection"][tables["model_selection"]["selected"]]
    print(f"model selection: {chosen['model_type'].value_counts().to_dict()}, passed {int(chosen['passed'].sum())}"
          f" of {len(chosen)}")
    fits.to_csv(run_directory / "fit_metrics.csv", index=False)

    in_use = model_in_use(tables["model_selection"], read_model_choice(model_choice_path(PASTAS, VALIDATED)),
                          series=fits["meetreeks_id"])
    if args.plots == "in_use":
        ok = fits[fits["success"].astype(bool)][["meetreeks_id", "series_name", "model_type"]]
        drawn = plot_models(str(run_directory), in_use.merge(ok, on=["meetreeks_id", "model_type"]),
                            exclude_first=args.exclude_first)
        print(f"figures of the models in use: {drawn['status'].value_counts().to_dict()}")

    if args.export:
        out = results.export_dir(PASTAS, VALIDATED)
        out.mkdir(parents=True, exist_ok=True)
        for kind, filename in results.EXPORT_FILES.items():
            frame = tables.get(kind)
            if frame is None or frame.empty:
                print(f"  export: no {kind}, {filename} left as it is")
                continue
            path = out / filename
            frame.to_parquet(path, index=False) if filename.endswith(".parquet") else frame.to_csv(path, index=False)
            print(f"  export: {kind} -> {path.relative_to(REPO).as_posix()} ({len(frame)} rows)")
        stems = None if args.plots == "all" else stems_in_use(in_use, fits[["meetreeks_id", "series_name"]])
        counts = results.sync_plots(base, results.plots_dir(PASTAS, VALIDATED), stems=stems)
        print(f"  export: plots {counts}")


if __name__ == "__main__":
    main()
