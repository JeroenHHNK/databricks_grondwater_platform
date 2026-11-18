"""Which model a series uses: the "passed" stamp, the runaway check and the pick.

Every series is fitted with several model types (``tarso_pipeline.fit.DEFAULT_MODEL_TYPES``).
This module ranks them per series from the rows the pipeline already writes
(``fit_metrics``, ``model_checks`` and the extremes summary) and produces the
``<prefix>_model_selection`` rows: one per series x model type, ``selected`` on one of them.

The stamp ("passed")
--------------------
``passed`` = the Brakenhoff (2022) checks that say something about a model fitted without a
noise model: R2 >= 0.7, the response memory (t95 < half the calibration period), the gain
(|A| > 1.96 sigma) and no parameter on its bounds. The runs test on the residuals is kept
apart (``pass_runs``): without a noise model daily residuals are always autocorrelated, so
it fails everywhere, and on the validated series an AR(1) or ARMA(1,1) noise model did not
change that (2-5 of 25 passed).

The runaway check
-----------------
A model can fit the calibration years and still go somewhere absurd on the IMPREX ensemble
(a Berendrecht model reached +21.9 m NAP). ``runaway`` = the empirical T=100 level of a tail
more than ``RUNAWAY_MARGIN_M`` beyond the observed range of the series. Unknown (no extremes
run) counts as no runaway.

The pick, per series
--------------------
1. Candidates: the fits that succeeded and did not run away.
2. If any candidate passed, only those; otherwise all of them (``tier`` says which).
3. Drop a candidate whose ``r2_holdout`` is more than ``HOLDOUT_TOLERANCE`` below Tarso's:
   a lower AIC bought with worse prediction is overfitting (when both are known).
4. Lowest AIC wins, except that a model with fewer parameters within ``AIC_TOLERANCE`` of
   the lowest wins over it: complexity has to earn its place.

AIC is only comparable between fits of the same series on the same observations with the
same noise setting; the pipeline fits all model types of a series in one run, so it is.
A person can overrule the pick in the app (``model_choice``, kept in git like the family
pick); ``model_in_use`` applies that.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import numpy as np
import pandas as pd

AIC_TOLERANCE = 2.0
HOLDOUT_TOLERANCE = 0.05
RUNAWAY_MARGIN_M = 1.5
STAMP_CHECKS = ("rsq", "memory", "gain", "bounds")
TIER_PASSED, TIER_BEST_EFFORT = "passed", "best_effort"

MODEL_SELECTION_SCHEMA: dict[str, str] = {
    "run_id": "string",
    "meetreeks_id": "string",
    "series_name": "string",
    "model_type": "string",
    "recharge": "string",
    "rfunc": "string",
    "n_param": "int",
    "noise_model": "string",
    "r2": "double",
    "r2_holdout": "double",
    "evp": "double",
    "rmse": "double",
    "aic": "double",
    "d_aic": "double",         # minus the lowest AIC of the series
    "d_aic_tarso": "double",   # minus Tarso's AIC (negative = better than Tarso)
    "pass_rsq": "boolean",
    "pass_memory": "boolean",
    "pass_gain": "boolean",
    "pass_bounds": "boolean",
    "pass_runs": "boolean",
    "passed": "boolean",       # the stamp: rsq, memory, gain and bounds
    "min_value": "double",
    "max_value": "double",
    "dry_T100": "double",
    "wet_T100": "double",
    "runaway": "boolean",
    "eligible": "boolean",
    "tier": "string",
    "selected": "boolean",
    "reason": "string",
    "selected_at": "timestamp",
}


def check_kind(check: str) -> str | None:
    """The kind of one ``model_checks.check`` label (see ``model_checks.brakenhoff_checks``)."""
    check = str(check)
    if check.startswith("rsq"):
        return "rsq"
    if check.startswith("t95"):
        return "memory"
    if check.startswith("|"):
        return "gain"
    if check.startswith("Bounds"):
        return "bounds"
    if check.startswith("runs"):
        return "runs"
    return None


def check_summary(checks: pd.DataFrame) -> pd.DataFrame:
    """Per (meetreeks_id, model_type): ``pass_<kind>`` (all checks of that kind passed) and
    ``passed`` (the stamp). A checklist that errored passes nothing."""
    keys = ["meetreeks_id", "model_type"]
    columns = [*keys, *(f"pass_{k}" for k in (*STAMP_CHECKS, "runs")), "passed"]
    if checks is None or checks.empty:
        return pd.DataFrame(columns=columns)
    frame = checks.assign(kind=checks["check"].map(check_kind),
                          ok=checks["passed"].astype(str).str.lower().eq("true"))
    errored = frame[frame["check"] == "checklist_error"][keys].drop_duplicates()
    wide = (frame.dropna(subset=["kind"]).groupby([*keys, "kind"])["ok"].all()
            .unstack("kind").reindex(columns=[*STAMP_CHECKS, "runs"]))
    wide = wide.astype("boolean").fillna(False).astype(bool).add_prefix("pass_").reset_index()
    if not errored.empty:
        wide = pd.concat([wide, errored], ignore_index=True).drop_duplicates(keys, keep="first")
        for column in [c for c in wide.columns if c.startswith("pass_")]:
            wide[column] = wide[column].astype("boolean").fillna(False).astype(bool)
    wide["passed"] = wide[[f"pass_{k}" for k in STAMP_CHECKS]].all(axis=1)
    return wide[columns]


def runaway(dry_T100, wet_T100, min_value, max_value, margin: float = RUNAWAY_MARGIN_M) -> bool:
    """A T=100 level more than ``margin`` beyond the observed range; unknown = False."""
    dry = pd.notna(dry_T100) and pd.notna(min_value) and float(dry_T100) < float(min_value) - margin
    wet = pd.notna(wet_T100) and pd.notna(max_value) and float(wet_T100) > float(max_value) + margin
    return bool(dry or wet)


def _pick(group: pd.DataFrame) -> tuple[object, str, str]:
    """(index of the pick, tier, reason) within one series; index None if nothing can be picked."""
    eligible = group[group["eligible"]]
    if eligible.empty:
        return None, "", "geen bruikbaar model (alle fits mislukt of weggelopen)"
    passed = eligible[eligible["passed"]]
    tier, pool = (TIER_PASSED, passed) if not passed.empty else (TIER_BEST_EFFORT, eligible)
    tarso = group[(group["model_type"] == "tarso") & group["r2_holdout"].notna()]
    dropped = ""
    if not tarso.empty and pool["r2_holdout"].notna().any():
        floor = float(tarso["r2_holdout"].iloc[0]) - HOLDOUT_TOLERANCE
        kept = pool[pool["r2_holdout"].isna() | (pool["r2_holdout"] >= floor)]
        if not kept.empty and len(kept) < len(pool):
            dropped = f"; {len(pool) - len(kept)} afgevallen op R2 holdout"
            pool = kept
    lowest = pool["aic"].min()
    near = pool[pool["aic"] <= lowest + AIC_TOLERANCE].sort_values(["n_param", "aic"])
    best = near.index[0]
    simpler = near.loc[best, "aic"] > lowest
    why = ("haalt de checks" if tier == TIER_PASSED else "haalt de checks niet; beste beschikbare")
    why += (f", minder parameters binnen {AIC_TOLERANCE:g} AIC van de laagste" if simpler else ", laagste AIC")
    return best, tier, why + dropped


def select_models(metrics: pd.DataFrame, checks: pd.DataFrame | None = None,
                  extremes: pd.DataFrame | None = None, run_id: str | None = None) -> pd.DataFrame:
    """The ``<prefix>_model_selection`` rows for every series in ``metrics``.

    ``metrics``: ``fit_metrics`` rows (the latest run per series and model type);
    ``checks``: ``model_checks`` rows; ``extremes``: ``extremes_summary`` rows (for the
    runaway check; ``dr_emp_T100`` / ``wet_emp_T100``). ``run_id`` defaults to the newest
    run id in ``metrics``.
    """
    columns = list(MODEL_SELECTION_SCHEMA)
    if metrics is None or metrics.empty:
        return pd.DataFrame(columns=columns)
    keys = ["meetreeks_id", "model_type"]
    frame = metrics.copy()
    frame = frame[frame["success"].astype(str).str.lower().eq("true")]
    for column in ("recharge", "rfunc", "n_param", "noise_model", "r2_holdout", "evp", "rmse"):
        if column not in frame:
            frame[column] = None
    frame = frame.merge(check_summary(checks), on=keys, how="left")
    for column in [c for c in MODEL_SELECTION_SCHEMA if c.startswith("pass")]:
        frame[column] = frame[column].astype("boolean").fillna(False).astype(bool)
    if extremes is not None and not extremes.empty:
        ext = extremes.rename(columns={"dr_emp_T100": "dry_T100", "wet_emp_T100": "wet_T100"})
        ext = ext.drop_duplicates(keys, keep="last")[[*keys, "dry_T100", "wet_T100"]]
        frame = frame.merge(ext, on=keys, how="left")
    else:
        frame["dry_T100"], frame["wet_T100"] = np.nan, np.nan
    for column in ("aic", "r2", "r2_holdout", "n_param", "min_value", "max_value", "dry_T100", "wet_T100"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame["runaway"] = [runaway(d, w, lo, hi) for d, w, lo, hi in
                        zip(frame["dry_T100"], frame["wet_T100"], frame["min_value"], frame["max_value"])]
    frame["eligible"] = frame["aic"].notna() & ~frame["runaway"]
    frame["n_param"] = frame["n_param"].fillna(99)

    frame["d_aic"] = frame["aic"] - frame.groupby("meetreeks_id")["aic"].transform("min")
    tarso_aic = frame[frame["model_type"] == "tarso"].set_index("meetreeks_id")["aic"]
    frame["d_aic_tarso"] = frame["aic"] - frame["meetreeks_id"].map(tarso_aic)
    frame["selected"], frame["tier"], frame["reason"] = False, "", ""
    labels = {"rsq": "R2", "memory": "geheugen", "gain": "gain", "bounds": "grenzen"}
    for _, group in frame.groupby("meetreeks_id"):
        best, tier, why = _pick(group)
        frame.loc[group.index, "tier"] = tier
        if best is None:
            frame.loc[group.index, "reason"] = why
            continue
        for i in group.index.drop(best):
            failed = [labels[k] for k in STAMP_CHECKS if not frame.at[i, f"pass_{k}"]]
            delta = frame.at[i, "aic"] - frame.at[best, "aic"]
            frame.at[i, "reason"] = (f"haalt niet: {', '.join(failed)}" if failed and tier == TIER_PASSED
                                     else f"AIC {delta:+.1f} t.o.v. de keuze")
        frame.loc[best, ["selected", "reason"]] = [True, why]
    frame.loc[frame["runaway"], "reason"] = "loopt weg in de extremen (T=100 > 1,5 m buiten het gemeten bereik)"
    frame["n_param"] = frame["n_param"].where(frame["n_param"] != 99).astype("Int64")
    frame["run_id"] = run_id or str(metrics["run_id"].max())
    frame["selected_at"] = dt.datetime.now()
    return frame.reindex(columns=columns).sort_values(["meetreeks_id", "aic"]).reset_index(drop=True)


# --- the person's pick, in git ---------------------------------------------------------

MODEL_CHOICE_DIRNAME = "model_choice"
MODEL_CHOICE_COLUMNS = ["meetreeks_id", "series_name", "model_type", "note", "chosen_by", "chosen_at"]
AUTOMATISCH, HANDMATIG, STANDAARD = "automatisch", "handmatig", "standaard"


def model_choice_path(root: str | Path, basis: str) -> Path:
    """``<pastas>/model_choice/<prefix>_model_choice.csv``, written by the app, in git."""
    from tarso_pipeline.paths import TABLE_PREFIX, check_basis

    return Path(root) / MODEL_CHOICE_DIRNAME / f"{TABLE_PREFIX[check_basis(basis)]}_model_choice.csv"


def read_model_choice(path: str | Path) -> pd.DataFrame:
    path = Path(path)
    if not path.is_file():
        return pd.DataFrame(columns=MODEL_CHOICE_COLUMNS)
    frame = pd.read_csv(path, dtype=str, keep_default_na=False, encoding="utf-8-sig")
    for column in MODEL_CHOICE_COLUMNS:
        if column not in frame:
            frame[column] = ""
    return frame[MODEL_CHOICE_COLUMNS]


def set_model_choice(choice: pd.DataFrame, *, meetreeks_id: str, series_name: str, model_type: str | None,
                     note: str = "", chosen_by: str = "", chosen_at: str = "") -> pd.DataFrame:
    """One pick per series; ``model_type=None`` removes it (back to the automatic pick)."""
    from tarso_pipeline.fit import MODEL_TYPES

    if model_type is not None and model_type not in MODEL_TYPES:
        raise ValueError(f"model_type must be one of {MODEL_TYPES}, got {model_type!r}")
    out = choice[choice["meetreeks_id"] != meetreeks_id]
    if model_type is not None:
        out = pd.concat([out, pd.DataFrame([{
            "meetreeks_id": meetreeks_id, "series_name": series_name, "model_type": model_type,
            "note": note, "chosen_by": chosen_by, "chosen_at": chosen_at}])], ignore_index=True)
    return out.sort_values("meetreeks_id").reset_index(drop=True)[MODEL_CHOICE_COLUMNS]


def write_model_choice(path: str | Path, choice: pd.DataFrame) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    choice[MODEL_CHOICE_COLUMNS].to_csv(tmp, index=False, encoding="utf-8-sig")
    tmp.replace(path)


def model_in_use(selection: pd.DataFrame, choice: pd.DataFrame | None = None,
                 series: pd.Series | None = None, default: str = "tarso") -> pd.DataFrame:
    """Per series the model type that applies and why: ``meetreeks_id, model_type, bron``.

    A person's pick (``choice``) first, then the automatic pick (``selection.selected``),
    then ``default`` for the ``series`` that have neither (e.g. a run from before the
    selection existed).
    """
    parts = []
    if choice is not None and not choice.empty:
        picked = choice[choice["model_type"].fillna("").astype(str) != ""]
        parts.append(picked[["meetreeks_id", "model_type"]].assign(bron=HANDMATIG))
    if selection is not None and not selection.empty:
        # One selection run covers every model type of a series: only its latest run counts.
        latest = selection.groupby("meetreeks_id")["run_id"].transform("max") if "run_id" in selection else None
        current = selection if latest is None else selection[selection["run_id"] == latest]
        chosen = current[current["selected"].astype(str).str.lower().eq("true")]
        parts.append(chosen[["meetreeks_id", "model_type"]].assign(bron=AUTOMATISCH))
    if series is not None:
        parts.append(pd.DataFrame({"meetreeks_id": pd.Series(series).dropna().unique()})
                     .assign(model_type=default, bron=STANDAARD))
    if not parts:
        return pd.DataFrame(columns=["meetreeks_id", "model_type", "bron"])
    return pd.concat(parts, ignore_index=True).drop_duplicates("meetreeks_id", keep="first").reset_index(drop=True)


def rows_to_frame(rows) -> pd.DataFrame:
    return pd.DataFrame(rows).reindex(columns=list(MODEL_SELECTION_SCHEMA))


def spark_schema() -> str:
    return ", ".join(f"{name} {dtype}" for name, dtype in MODEL_SELECTION_SCHEMA.items())
