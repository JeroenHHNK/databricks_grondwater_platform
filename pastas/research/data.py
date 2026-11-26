"""The 89 validated series and the KNMI forcing, cleaned the way the pipeline cleans them.

The heads come from ``scripts/run_local.py::validated_heads`` (the review stores, approved
hours only) and go through ``tarso_pipeline.clean.clean_head`` and the same guards as
``fit_series`` (``min_obs`` 100, ``min_days`` 365, trimmed to the forcing coverage).
"""

from __future__ import annotations

import importlib.util
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from tarso_pipeline.clean import clean_head
from tarso_pipeline.forcing import check_coverage, forcing_file, load_forcing_pair
from tarso_pipeline.paths import series_name_from_id

PASTAS = Path(__file__).resolve().parents[1]
REPO = PASTAS.parent
SELECTION = PASTAS / "results_export" / "valid_pastas" / "model_selection.csv"
COMMITTED_METRICS = PASTAS / "results_export" / "valid_pastas" / "fit_metrics.csv"


@dataclass(frozen=True)
class Series:
    meetreeks_id: str
    name: str
    head: pd.Series   # daily means, observed days only
    tmin: str         # the calibration start the pipeline hands Pastas


def _run_local():
    spec = importlib.util.spec_from_file_location("run_local", PASTAS / "scripts" / "run_local.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_forcing(station: str = "249") -> tuple[pd.Series, pd.Series]:
    return load_forcing_pair(forcing_file(str(PASTAS / "data" / "forcing"), station), units="m")


def raw_heads(ids: set[str]) -> dict[str, pd.DataFrame]:
    """``meetreeks_id -> frame(tijdstempel, waarde)`` from the raw export (not validated)."""
    objects = pd.read_csv(REPO / "data_validation" / "input_output" / "output_databricks" / "metadata" / "object_data.csv",
                          usecols=["filename", "meetreeks_id"]).drop_duplicates("meetreeks_id")
    folder = REPO / "data_validation" / "input_output" / "output_databricks" / "time_series_data"
    out = {}
    for row in objects[objects["meetreeks_id"].isin(ids)].itertuples():
        path = folder / f"{row.filename}.csv"
        if path.exists():
            frame = pd.read_csv(path)
            out[row.meetreeks_id] = pd.DataFrame({"tijdstempel": pd.to_datetime(frame.iloc[:, 0]),
                                                  "waarde": pd.to_numeric(frame.iloc[:, 1], errors="coerce")})
    return out


def load_series(precipitation: pd.Series, min_obs: int = 100, min_days: int = 365,
                limit: int | None = None, heads: dict | None = None) -> list[Series]:
    out = []
    heads = _run_local().validated_heads() if heads is None else heads
    for meetreeks_id, raw in heads.items():
        cleaned = clean_head(raw)
        if cleaned.n_final < min_obs or cleaned.span_days < min_days:
            continue
        coverage = check_coverage(precipitation, cleaned.series, warmup_days=3650)
        if not coverage.usable:
            continue
        head = cleaned.series.loc[coverage.tmin:coverage.tmax]
        out.append(Series(meetreeks_id, series_name_from_id(meetreeks_id), head,
                          coverage.tmin.strftime("%Y-%m-%d")))
        if limit and len(out) >= limit:
            break
    return out


def model_in_use() -> dict[str, str]:
    """``meetreeks_id -> model_type`` the committed pipeline run picked (``selected``)."""
    selection = pd.read_csv(SELECTION)
    picked = selection[selection["selected"].astype(str).str.lower() == "true"]
    return dict(zip(picked["meetreeks_id"], picked["model_type"]))
