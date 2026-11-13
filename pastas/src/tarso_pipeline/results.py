"""Reading the results back: the latest run per series, and the family that applies.

Shared by the Databricks notebooks (export, family choice) and the Streamlit app, so the
app and Databricks agree on what "the result for this series" is. Pure SQL strings and
pandas; no Spark, no Pastas.

Tables accumulate one set of rows per run (``run_id``, a sortable UTC timestamp). The
result for a series is the **latest run that produced a row for it**, per model type:
``latest_run_sql``.

The levels that apply per tail (``chosen_levels``): up to T=100 the empirical level of the
pooled years, past it the family a person picked (``gpd`` or ``exp``) at the threshold they
picked (``<prefix>_family_choice``, kept in git as ``family_choice/<prefix>_family_choice.csv``).
A tail without a pick has none.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

import pandas as pd

from tarso_pipeline.evt import EMPIRICAL_MAX_T
from tarso_pipeline.paths import (
    CONFIDENCE_PLOTS_DIR,
    DIAGNOSTICS_DIR,
    QQ_PLOTS_DIR,
    RESULTS_DIR,
    RETURN_PLOTS_DIR,
    TABLE_PREFIX,
    THRESHOLD_PLOTS_DIR,
    check_basis,
)

EXPORT_DIRNAME = "results_export"
# The figures, copied from the run folders into results_export/<prefix>/plots/ as
# <series_name>_<model_type>_<kind>.png: one file per model and kind, from the most recent
# run. Runs from before the files carried their model type wrote <series_name>_<kind>.png
# for the extremes (Tarso was the only model then); ``find_plot`` falls back to those.
PLOTS_DIRNAME = "plots"
PLOT_KINDS = {
    # kind:          (folder in the run folder, suffix of the file run_extremes writes)
    "return_periods": (RETURN_PLOTS_DIR, "_return_periods.png"),
    "qq": (QQ_PLOTS_DIR, "_qq.png"),
    "threshold": (THRESHOLD_PLOTS_DIR, "_threshold.png"),
    "confidence": (CONFIDENCE_PLOTS_DIR, "_return_level.png"),  # explore_shape_and_uncertainty
    # The fit itself (fit_pastas_models): ml.plots.results() and ml.plots.diagnostics().
    "results": (RESULTS_DIR, "_results.png"),
    "diagnostics": (DIAGNOSTICS_DIR, "_diagnostics.png"),
}
FAMILY_CHOICE_DIRNAME = "family_choice"
# What the app needs, and the file each is exported to (export_results.py).
EXPORT_FILES = {
    "fit_metrics": "fit_metrics.csv",
    "model_checks": "model_checks.csv",
    "evt_selection": "evt_selection.csv",
    "return_levels": "return_levels.csv",
    "simulations": "simulations.parquet",
    "model_selection": "model_selection.csv",
}
FAMILIES = ("gpd", "exp", "gev", "emp")
# What a person can pick per tail: how to extrapolate past EMPIRICAL_MAX_T.
PICKABLE = ("gpd", "exp")
FAMILY_LABELS = {"gpd": "GPD (staart vlakt af)", "exp": "exponentieel (staart vlakt niet af)",
                 "gev": "GEV (alle jaren, vergelijking)", "emp": "empirisch"}
TAIL_LABELS = {"dry": "droog (laagste standen)", "wet": "nat (hoogste standen)"}
FAMILY_CHOICE_COLUMNS = ["meetreeks_id", "series_name", "model_type", "tail", "family", "n_kept", "note",
                         "chosen_by", "chosen_at"]
EMPIRISCH, FAMILIE = "empirisch", "familie"

_TABLE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*){2}$")
_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


def _check_table(table: str) -> str:
    if not _TABLE.fullmatch(table):
        raise ValueError(f"not a three-part table name: {table!r}")
    return table


def latest_run_sql(table: str, keys: tuple[str, ...] = ("meetreeks_id", "model_type"),
                   meetreeks_id: str | None = None) -> str:
    """Every row of the latest run per ``keys`` (optionally for one series only)."""
    _check_table(table)
    where = ""
    if meetreeks_id is not None:
        if not _ID.fullmatch(meetreeks_id):
            raise ValueError(f"meetreeks_id must be a plain hash, got {meetreeks_id!r}")
        where = f"WHERE meetreeks_id = '{meetreeks_id}'"
    on = " AND ".join(f"t.{k} = l.{k}" for k in (*keys, "run_id"))
    cols = ", ".join(keys)
    return f"""
        SELECT t.* FROM {table} t
        JOIN (SELECT {cols}, max(run_id) AS run_id FROM {table} {where} GROUP BY {cols}) l
          ON {on}
    """


def latest_rows(frame: pd.DataFrame, keys: tuple[str, ...] = ("meetreeks_id", "model_type")) -> pd.DataFrame:
    """``latest_run_sql`` for a frame already in pandas."""
    if frame.empty:
        return frame
    latest = frame.groupby(list(keys))["run_id"].transform("max")
    return frame[frame["run_id"] == latest].reset_index(drop=True)


def export_dir(root: str | Path, basis: str) -> Path:
    """``<pastas>/results_export/<prefix>/``, the copy the app reads without live access."""
    return Path(root) / EXPORT_DIRNAME / TABLE_PREFIX[check_basis(basis)]


def plots_dir(root: str | Path, basis: str) -> Path:
    """``<pastas>/results_export/<prefix>/plots/``."""
    return export_dir(root, basis) / PLOTS_DIRNAME


def plot_path(root: str | Path, basis: str, series_name: str, kind: str) -> Path:
    """Where the exported ``kind`` figure of one file stem lives (it may not exist).

    ``series_name`` is the stem: ``<series_name>_<model_type>`` (``find_plot`` builds it).
    """
    if kind not in PLOT_KINDS:
        raise ValueError(f"kind must be one of {tuple(PLOT_KINDS)}, got {kind!r}")
    return plots_dir(root, basis) / f"{series_name}_{kind}.png"


def find_plot(root: str | Path, basis: str, series_name: str, model_type: str, kind: str) -> Path | None:
    """The exported ``kind`` figure of one model, or None.

    ``<series>_<model_type>_<kind>.png``; for Tarso also the older ``<series>_<kind>.png``.
    """
    candidates = [plot_path(root, basis, f"{series_name}_{model_type}", kind)]
    if model_type == "tarso":
        candidates.append(plot_path(root, basis, series_name, kind))
    return next((p for p in candidates if p.is_file()), None)


def _check_kinds(kinds) -> tuple[str, ...]:
    kinds = tuple(PLOT_KINDS) if kinds is None else tuple(kinds)
    unknown = [k for k in kinds if k not in PLOT_KINDS]
    if unknown:
        raise ValueError(f"unknown plot kind(s) {unknown}; use {tuple(PLOT_KINDS)}")
    return kinds


def latest_plots(basis_volume_dir: str | Path, kinds=None) -> dict[tuple[str, str], Path]:
    """The newest figure per (file stem, kind) over every run folder of one data basis.

    The stem is ``<series_name>_<model_type>`` (or a bare series name for an old run).

    Newest by modification time, not by folder name, so a run folder with a custom name
    (``run_folder`` widget) counts too. ``kinds`` limits it to some of ``PLOT_KINDS``.
    """
    found: dict[tuple[str, str], Path] = {}
    base = Path(basis_volume_dir)
    if not base.is_dir():
        return found
    for kind in _check_kinds(kinds):
        folder, suffix = PLOT_KINDS[kind]
        for path in base.glob(f"*/{folder}/*{suffix}"):
            key = (path.name[: -len(suffix)], kind)
            if key not in found or path.stat().st_mtime > found[key].stat().st_mtime:
                found[key] = path
    # An old-style <series>_<kind>.png (Tarso) is superseded once <series>_tarso_<kind>.png exists.
    return {(stem, kind): path for (stem, kind), path in found.items()
            if (f"{stem}_tarso", kind) not in found}


def sync_plots(basis_volume_dir: str | Path, target: str | Path, kinds=None, stems=None) -> dict[str, int]:
    """Mirror :func:`latest_plots` into ``target``: one PNG per model and kind.

    A file whose bytes did not change is left alone (no churn in git); a PNG in
    ``target`` that no run folder has any more is removed. Only the ``kinds`` synced
    are touched: leaving a kind out keeps its files as they are. ``stems`` (a set of
    ``<series_name>_<model_type>``, ``model_plots.stems_in_use``) exports only those
    models and removes the figures of the others. Returns the counts.
    """
    kinds = _check_kinds(kinds)
    target = Path(target)
    target.mkdir(parents=True, exist_ok=True)
    wanted = {f"{series}_{kind}.png": source
              for (series, kind), source in latest_plots(basis_volume_dir, kinds).items()
              if stems is None or series in stems}
    counts = {"copied": 0, "unchanged": 0, "removed": 0}
    for name, source in sorted(wanted.items()):
        dest = target / name
        if dest.is_file() and dest.stat().st_size == source.stat().st_size \
                and dest.read_bytes() == source.read_bytes():
            counts["unchanged"] += 1
            continue
        shutil.copyfile(source, dest)
        counts["copied"] += 1
    for stale in target.glob("*.png"):
        if stale.name not in wanted and stale.name.endswith(tuple(f"_{k}.png" for k in kinds)):
            stale.unlink()
            counts["removed"] += 1
    return counts


def family_choice_path(root: str | Path, basis: str) -> Path:
    """``<pastas>/family_choice/<prefix>_family_choice.csv``, written by the app, in git."""
    return Path(root) / FAMILY_CHOICE_DIRNAME / f"{TABLE_PREFIX[check_basis(basis)]}_family_choice.csv"


def read_family_choice(path: str | Path) -> pd.DataFrame:
    path = Path(path)
    if not path.is_file():
        return pd.DataFrame(columns=FAMILY_CHOICE_COLUMNS)
    frame = pd.read_csv(path, dtype=str, keep_default_na=False, encoding="utf-8-sig")
    for column in FAMILY_CHOICE_COLUMNS:
        if column not in frame:
            frame[column] = ""
    return frame[FAMILY_CHOICE_COLUMNS]


def set_family_choice(choice: pd.DataFrame, *, meetreeks_id: str, series_name: str, model_type: str,
                      tail: str, family: str | None, n_kept: int | None = None, note: str = "",
                      chosen_by: str = "", chosen_at: str = "") -> pd.DataFrame:
    """One pick per (meetreeks_id, model_type, tail); ``family=None`` removes it (no family).

    ``n_kept`` is the threshold: the number of pooled years above it (blank = the
    default threshold of the extremes run).
    """
    if family is not None and family not in PICKABLE:
        raise ValueError(f"family must be one of {PICKABLE}, got {family!r}")
    if tail not in TAIL_LABELS:
        raise ValueError(f"tail must be one of {tuple(TAIL_LABELS)}, got {tail!r}")
    same = ((choice["meetreeks_id"] == meetreeks_id) & (choice["model_type"] == model_type)
            & (choice["tail"] == tail))
    out = choice[~same]
    if family is not None:
        out = pd.concat([out, pd.DataFrame([{
            "meetreeks_id": meetreeks_id, "series_name": series_name, "model_type": model_type,
            "tail": tail, "family": family, "n_kept": "" if n_kept is None else str(int(n_kept)),
            "note": note, "chosen_by": chosen_by, "chosen_at": chosen_at,
        }])], ignore_index=True)
    return out.sort_values(["meetreeks_id", "model_type", "tail"]).reset_index(drop=True)[FAMILY_CHOICE_COLUMNS]


def write_family_choice(path: str | Path, choice: pd.DataFrame) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    choice[FAMILY_CHOICE_COLUMNS].to_csv(tmp, index=False, encoding="utf-8-sig")
    tmp.replace(path)


def chosen_levels(levels: pd.DataFrame, choice: pd.DataFrame,
                  empirical_max_t: int = EMPIRICAL_MAX_T) -> pd.DataFrame:
    """The return levels that apply per tail, for the tails a person picked a family for.

    Up to ``empirical_max_t`` the empirical level (``emp``): with ~704 pooled years no
    distribution is needed there. Past it the picked family (``gpd`` / ``exp``) at the
    picked threshold (``n_kept``; blank = the default one). ``bron`` says which. A tail
    without a pick, or with a family that cannot be picked any more (``gev``), has none.
    """
    keys = ["meetreeks_id", "model_type", "tail"]
    extra = ["picked_family", "picked_kept", "bron"]
    picks = choice[choice["family"].fillna("").astype(str).isin(PICKABLE)]
    picks = picks.drop_duplicates(keys, keep="last")[[*keys, "family", "n_kept"]].rename(
        columns={"family": "picked_family", "n_kept": "picked_kept"})
    if picks.empty or levels.empty:
        return pd.DataFrame(columns=[*levels.columns, *extra])
    merged = levels.merge(picks, on=keys, how="inner")
    period = pd.to_numeric(merged["return_period"])
    emp = merged[(merged["family"] == "emp") & (period <= empirical_max_t)].assign(bron=EMPIRISCH)
    fam = merged[(merged["family"] == merged["picked_family"]) & (period > empirical_max_t)]
    if "n_kept" in fam:
        wanted = pd.to_numeric(fam["picked_kept"], errors="coerce")
        default = (fam["default_kept"].astype(str).str.lower().eq("true") if "default_kept" in fam
                   else pd.Series(True, index=fam.index))
        fam = fam[(wanted.isna() & default) | (pd.to_numeric(fam["n_kept"], errors="coerce") == wanted)]
    fam = fam.assign(bron=FAMILIE)
    return pd.concat([emp, fam], ignore_index=True)
