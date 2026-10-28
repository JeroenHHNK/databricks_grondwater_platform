"""The `pastas_model_checks` rows: Brakenhoff et al. (2022) via ``ps.check``.

One row per check per fit, so a series' verdict is a ``GROUP BY`` and every
individual reason for a "no" stays visible.

Why not ``ps.check.get_checks_literature("brakenhoff_2022")`` as-is
-------------------------------------------------------------------
Two reasons, both verified against the installed versions:

* **It breaks on Tarso.** pastas 1.14's list checks the gain with
  ``uncertainty_gain``, which looks up ``<stressmodel>_A``. Tarso has two gains,
  ``Tarso_A0`` and ``Tarso_A1``, so it raises ``KeyError: 'Tarso_A'``.
* **It is not stable across releases.** The notebook pins pastas 1.12, which has
  no ``get_checks_literature`` and whose Brakenhoff list tests the uncertainty
  of *every* parameter -- drainage levels in m NAP included -- rather than the
  gain only.

So the list is built here from the check functions both versions share, with
the 1.14 intent (gain uncertainty only), and the gains found by name: one for
``recharge``, two for ``tarso``.

Tarso's second drainage level
-----------------------------
``response_memory`` reaches the response through ``rfunc.step(p)``, and the
Exponential reads ``p[0], p[1]`` -- for Tarso that is ``A0, a0``, level 0 only.
``tarso_level1_memory`` adds the same test for ``a1``, so both levels' memory
is held to the same bar.

The model is fitted without a noise model, so the residuals stand in for the
noise. The runs test reads them directly; the 1.96 sigma gain test assumes they
are white, so a failed runs test means that one should not be relied on either.
"""

from __future__ import annotations

import datetime as dt
import re

import numpy as np
import pandas as pd

CHECKLIST_NAME = "brakenhoff_2022"

# Brakenhoff et al. (2022), as pastas implements it.
RSQ_THRESHOLD = 0.7
MEMORY_CUTOFF = 0.95
MEMORY_FACTOR = 0.5
RUNS_P_THRESHOLD = 0.05
GAIN_N_STD = 1.96

# Column -> Spark type, in order. ``passed`` is pastas' ``pass`` column, renamed
# only because ``pass`` is a Python keyword.
MODEL_CHECKS_SCHEMA: dict[str, str] = {
    "run_id": "string",
    "meetreeks_id": "string",
    "series_name": "string",
    "model_type": "string",
    "checklist": "string",
    "check_order": "int",
    "check": "string",
    "statistic": "double",
    "operator": "string",
    # A scalar threshold, or a (min, max) pair for the parameter bounds check.
    "threshold": "double",
    "threshold_min": "double",
    "threshold_max": "double",
    "dimensions": "string",
    "passed": "boolean",
    "comment": "string",
    "pastas_version": "string",
    "checked_at": "timestamp",
}

_GAIN = re.compile(r"_A\d*$")


def gain_parameters(model) -> list[str]:
    """``<stressmodel>_A`` for a recharge model, ``_A0`` and ``_A1`` for Tarso."""
    return [name for name in model.parameters.index if _GAIN.search(name)]


def tarso_level1_memory(
    model,
    cutoff: float = MEMORY_CUTOFF,
    factor_length_oseries: float = MEMORY_FACTOR,
) -> pd.DataFrame:
    """``response_memory`` for Tarso's second drainage level, in pastas' format."""
    calib_days = (model.settings["tmax"] - model.settings["tmin"]).days
    threshold = factor_length_oseries * calib_days
    rows = {}
    for name, sm in model.stressmodels.items():
        if getattr(sm, "_name", None) != "TarsoModel":
            continue
        memory = -model.parameters.loc[f"{name}_a1", "optimal"] * np.log(1.0 - cutoff)
        # Labelled and compared as pastas' own response_memory does it.
        rows[f"t{int(cutoff * 100)}_{name}_level1 < {factor_length_oseries} Δt_calib"] = [
            memory, "<", threshold, "[T]", bool(memory < threshold),
            f"t{int(cutoff * 100)} of the second drainage level",
        ]
    frame = pd.DataFrame.from_dict(
        rows, orient="index",
        columns=["statistic", "operator", "threshold", "dimensions", "pass", "comment"],
    )
    frame.index.name = "check"
    return frame


def brakenhoff_checks(model) -> list[dict]:
    """The Brakenhoff (2022) checklist, fitted to this model's parameters."""
    import pastas as ps

    checks = [
        {"func": ps.check.rsq_geq_threshold, "threshold": RSQ_THRESHOLD},
        {
            "func": ps.check.response_memory,
            "cutoff": MEMORY_CUTOFF,
            "factor_length_oseries": MEMORY_FACTOR,
        },
    ]
    if any(getattr(sm, "_name", None) == "TarsoModel" for sm in model.stressmodels.values()):
        checks.append({"func": tarso_level1_memory})
    checks += [
        {"func": ps.check.acf_runs_test, "p_threshold": RUNS_P_THRESHOLD},
        {
            "func": ps.check.uncertainty_parameters,
            "parameters": gain_parameters(model),
            "n_std": GAIN_N_STD,
        },
        {"func": ps.check.parameter_bounds},
    ]
    return checks


def _float(value) -> float | None:
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return None if np.isnan(value) else value


def _bool(value) -> bool | None:
    try:
        return None if pd.isna(value) else bool(value)
    except (TypeError, ValueError):
        return None


def frame_to_rows(frame: pd.DataFrame, identity: dict) -> list[dict]:
    """``ps.check.checklist`` output -> one schema row per check."""
    import pastas as ps

    checked_at = dt.datetime.now()
    rows = []
    for order, (check, result) in enumerate(frame.iterrows()):
        row = {name: None for name in MODEL_CHECKS_SCHEMA}
        row.update(identity)
        threshold = result["threshold"]
        if isinstance(threshold, (tuple, list)) and len(threshold) == 2:
            row["threshold_min"], row["threshold_max"] = map(_float, threshold)
        else:
            row["threshold"] = _float(threshold)
        row.update(
            {
                "checklist": CHECKLIST_NAME,
                "check_order": order,
                "check": str(check),
                "statistic": _float(result["statistic"]),
                "operator": None if result["operator"] is None else str(result["operator"]),
                "dimensions": None if result["dimensions"] is None else str(result["dimensions"]),
                "passed": _bool(result["pass"]),
                "comment": str(result["comment"]) if result["comment"] else None,
                "pastas_version": ps.__version__,
                "checked_at": checked_at,
            }
        )
        rows.append(row)
    return rows


def run_checks(model, identity: dict) -> list[dict]:
    """Run the checklist on a solved model. Never raises.

    ``identity`` carries ``run_id``, ``meetreeks_id``, ``series_name`` and
    ``model_type``. A checklist that throws becomes one row with
    ``check="checklist_error"`` and ``passed`` NULL -- not a pass, and not a lost
    series.
    """
    import pastas as ps

    try:
        frame = ps.check.checklist(model, brakenhoff_checks(model), report=False)
        return frame_to_rows(frame, identity)
    except Exception as exc:  # noqa: BLE001 - a failed check is data, not a crash
        row = {name: None for name in MODEL_CHECKS_SCHEMA}
        row.update(identity)
        row.update(
            {
                "checklist": CHECKLIST_NAME,
                "check_order": 0,
                "check": "checklist_error",
                "comment": f"{type(exc).__name__}: {exc}",
                "pastas_version": ps.__version__,
                "checked_at": dt.datetime.now(),
            }
        )
        return [row]


def rows_to_frame(rows: list[dict]) -> pd.DataFrame:
    """Stack rows with the columns pinned to ``MODEL_CHECKS_SCHEMA`` order."""
    return pd.DataFrame(rows, columns=list(MODEL_CHECKS_SCHEMA))


def spark_schema() -> str:
    """``MODEL_CHECKS_SCHEMA`` as a Spark DDL string.

    Names are backtick-quoted: ``check`` and ``comment`` are SQL keywords.
    """
    return ", ".join(f"`{name}` {dtype}" for name, dtype in MODEL_CHECKS_SCHEMA.items())
