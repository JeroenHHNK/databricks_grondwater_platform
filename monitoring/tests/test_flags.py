"""The monitoring flag counts are the review app's own checks, counted per window."""

import datetime as dt

import numpy as np
import pandas as pd

from local_validation.checks import validate_series
from monitoring_lib.flags import FLAG_SCHEMA, flag_counts, flag_counts_frame

AS_OF = dt.date(2026, 11, 17)
HOURS = 24 * 30


def _series(jump_at=None, bottom_at=None):
    ts = pd.date_range(pd.Timestamp(AS_OF) - pd.Timedelta(hours=HOURS), periods=HOURS, freq="h")
    head = -1.0 + 0.001 * np.sin(np.arange(HOURS) / 24)
    if jump_at is not None:
        head[jump_at:] += 0.5            # one v2 step of +0.5 m
    if bottom_at is not None:
        head[bottom_at] = -4.15          # at the filter bottom
    return pd.DataFrame({"ts": ts, "waarde": head, "is_validated": False})


def test_counts_match_validate_series():
    frame = _series(jump_at=100, bottom_at=200)
    row = flag_counts(frame, begin_filter=-3.1, eind_filter=-4.1, bovenkant_peilbuis=1.0, as_of=AS_OF)
    direct = validate_series(pd.Series(frame["waarde"].to_numpy(), index=pd.DatetimeIndex(frame["ts"])),
                             begin_filter=-3.1, eind_filter=-4.1, bovenkant_peilbuis=1.0)
    for layer in ("v1", "v2", "v3", "v4"):
        assert row[f"{layer}_totaal"] == int(direct[layer].notna().sum())
    assert row["rule_a_totaal"] == int(direct["rule_a"].sum())
    assert row["rule_b_totaal"] == int(direct["rule_b"].sum())
    assert row["uren_totaal"] == HOURS
    assert row["v2_totaal"] >= 1


def test_windows_and_validated_split():
    frame = _series(jump_at=HOURS - 3)   # the jump falls yesterday
    frame.loc[: HOURS // 2 - 1, "is_validated"] = True
    row = flag_counts(frame, eind_filter=-4.1, as_of=AS_OF)
    assert row["uren_gisteren"] == 24 and row["uren_7d"] == 24 * 7
    assert row["v2_gisteren"] == 1 and row["v2_7d"] == 1
    assert row["uren_gevalideerd"] == HOURS // 2
    assert row["verdacht_ongevalideerd"] >= 1
    assert row["aandeel_gevalideerd"] == 0.5


def test_rule_a_alone_is_not_suspect():
    frame = _series()
    frame.loc[50:, "waarde"] -= 0.04     # a drop of 0.04 m: below rule A (0.10) and v2 (-0.05)
    frame.loc[400:, "waarde"] -= 0.12    # 0.12 m: rule A and v2
    row = flag_counts(frame, as_of=AS_OF)
    assert row["rule_a_totaal"] == 1
    assert row["verdacht_totaal"] == row["v1_totaal"] + row["v2_totaal"] + row["v3_totaal"] + row["v4_totaal"]


def test_frame_has_the_schema_and_never_raises():
    frame = _series().assign(meetreeks_id="a" * 64, begin_filter=None, eind_filter=None, bovenkant_peilbuis=None)
    out = flag_counts_frame(frame, AS_OF)
    assert list(out.columns) == list(FLAG_SCHEMA) and out.loc[0, "meetreeks_id"] == "a" * 64
    broken = frame.assign(waarde="x")
    assert flag_counts_frame(broken, AS_OF).loc[0, "meetreeks_id"] == "a" * 64
