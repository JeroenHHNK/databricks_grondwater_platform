"""Per-series flag counts for the monitoring dashboard, with the review app's own rules.

The flags are not re-implemented in SQL: :func:`flag_counts` runs
``local_validation.checks.validate_series`` (v1-v4 and rules A/B, with its default
``Thresholds``) on one series' hourly heads, so the dashboard counts exactly what the
review app would flag. The notebook calls :func:`flag_counts_frame` per series, on the
driver, in batches of series (``build_monitoring_tables.py`` section 4).

"Verdacht" (suspect) = flagged by v1, v2, v3, v4 or rule B. Rule A (a sudden drop) is
counted on its own but left out of "verdacht": it is report-only in the review app,
because it disagreed with human reviewers on most hits (``checks.rule_a_sudden_drop``).
"""

from __future__ import annotations

import datetime as dt

import pandas as pd

from local_validation.checks import validate_series

LAYERS = ("v1", "v2", "v3", "v4")
WINDOWS = ("totaal", "7d", "gisteren")

# Column -> Spark type, in order (the notebook builds the table's schema from it).
FLAG_SCHEMA: dict[str, str] = {
    "meetreeks_id": "string",
    "as_of": "date",
    **{f"uren_{w}": "long" for w in WINDOWS},
    **{f"{flag}_{w}": "long" for w in WINDOWS for flag in (*LAYERS, "rule_a", "rule_b")},
    **{f"verdacht_{w}": "long" for w in WINDOWS},
    "uren_gevalideerd": "long",
    "verdacht_ongevalideerd": "long",
    "aandeel_verdacht": "double",
    "aandeel_gevalideerd": "double",
}


def _window_masks(index: pd.DatetimeIndex, as_of: dt.date) -> dict[str, pd.Series]:
    """Which hours fall in each window. ``as_of`` is today (UTC): yesterday = as_of - 1."""
    today = pd.Timestamp(as_of)
    hours = pd.Series(index, index=index)
    return {
        "totaal": pd.Series(True, index=index),
        "7d": (hours >= today - pd.Timedelta(days=7)) & (hours < today),
        "gisteren": (hours >= today - pd.Timedelta(days=1)) & (hours < today),
    }


def flag_counts(hourly: pd.DataFrame, *, begin_filter=None, eind_filter=None,
                bovenkant_peilbuis=None, as_of: dt.date) -> dict:
    """Flag counts of one series.

    ``hourly``: one row per hour with a value; columns ``ts`` (hour, UTC-naive),
    ``waarde`` (raw head, m NAP) and ``is_validated`` (the hour is in ``validated_meting``).
    """
    frame = hourly.dropna(subset=["waarde"]).sort_values("ts").drop_duplicates("ts", keep="last")
    raw = pd.Series(frame["waarde"].to_numpy(float), index=pd.DatetimeIndex(frame["ts"]))
    validated = pd.Series(frame["is_validated"].fillna(False).astype(bool).to_numpy(), index=raw.index)
    result = validate_series(raw, begin_filter=begin_filter, eind_filter=eind_filter,
                             bovenkant_peilbuis=bovenkant_peilbuis)
    hits = {layer: result[layer].notna() for layer in LAYERS}
    hits["rule_a"] = result["rule_a"].astype(bool)
    hits["rule_b"] = result["rule_b"].astype(bool)
    suspect = hits["v1"] | hits["v2"] | hits["v3"] | hits["v4"] | hits["rule_b"]

    row: dict = {"as_of": as_of}
    for window, mask in _window_masks(raw.index, as_of).items():
        row[f"uren_{window}"] = int(mask.sum())
        for flag, hit in hits.items():
            row[f"{flag}_{window}"] = int((hit & mask).sum())
        row[f"verdacht_{window}"] = int((suspect & mask).sum())
    row["uren_gevalideerd"] = int(validated.sum())
    row["verdacht_ongevalideerd"] = int((suspect & ~validated).sum())
    total = row["uren_totaal"]
    row["aandeel_verdacht"] = row["verdacht_totaal"] / total if total else None
    row["aandeel_gevalideerd"] = row["uren_gevalideerd"] / total if total else None
    return row


def flag_counts_frame(pdf: pd.DataFrame, as_of: dt.date) -> pd.DataFrame:
    """The hours of one series (with its metadata repeated) -> one row of flag counts.

    Columns in: ``meetreeks_id, ts, waarde, is_validated, begin_filter, eind_filter,
    bovenkant_peilbuis``. Never raises: a series the checks cannot handle gets its hour
    counts and zero flags, so one odd series cannot stop the job.
    """
    first = pdf.iloc[0]
    try:
        row = flag_counts(pdf, begin_filter=first["begin_filter"], eind_filter=first["eind_filter"],
                          bovenkant_peilbuis=first["bovenkant_peilbuis"], as_of=as_of)
    except Exception:  # noqa: BLE001
        row = {"as_of": as_of, "uren_totaal": int(pdf["waarde"].notna().sum())}
    row["meetreeks_id"] = first["meetreeks_id"]
    return pd.DataFrame([row]).reindex(columns=list(FLAG_SCHEMA))


def typed(frame: pd.DataFrame) -> pd.DataFrame:
    """Nullable dtypes per ``FLAG_SCHEMA``, so ``spark.createDataFrame`` accepts missing counts."""
    out = frame.reindex(columns=list(FLAG_SCHEMA)).copy()
    for name, kind in FLAG_SCHEMA.items():
        if kind == "long":
            out[name] = pd.to_numeric(out[name], errors="coerce").astype("Int64")
        elif kind == "double":
            out[name] = pd.to_numeric(out[name], errors="coerce").astype("float64")
        elif kind == "date":
            out[name] = pd.to_datetime(out[name]).dt.date
        else:
            out[name] = out[name].astype(object)
    return out


def spark_schema() -> str:
    return ", ".join(f"{name} {kind}" for name, kind in FLAG_SCHEMA.items())
