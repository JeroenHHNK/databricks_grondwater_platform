import datetime as dt

import pandas as pd
import pytest

from tarso_pipeline.knmi import (
    DEFAULT_STATION,
    FetchWindow,
    fetch_daily,
    parse_daily_csv,
    plan_window,
)

# A trimmed copy of a real response: banner comments, the station line, the
# variable descriptions, then the '# STN,...' header and padded data rows.
RESPONSE = """# SOURCE: ROYAL NETHERLANDS METEOROLOGICAL INSTITUTE (KNMI)
#
# STN         LON(east)   LAT(north)  ALT(m)      NAME
# 249         4.979       52.644      -2.40       Berkhout
# RH        : Etmaalsom van de neerslag (in 0.1 mm) (-1 voor <0.05 mm)
# EV24      : Referentiegewasverdamping (Makkink) (in 0.1 mm)
# STN,YYYYMMDD,   RH, EV24
  249,20200101,    0,    1
  249,20200102,   -1,    3
  249,20200103,   71,   12
"""

COMMENTS_ONLY = """# SOURCE: ROYAL NETHERLANDS METEOROLOGICAL INSTITUTE (KNMI)
# STN,YYYYMMDD,   RH, EV24
"""


class FakeResponse:
    def __init__(self, text):
        self.text = text

    def raise_for_status(self):
        return None


class FakeSession:
    """Records the POST it was given instead of making one."""

    def __init__(self, text=RESPONSE):
        self.text = text
        self.calls = []

    def post(self, url, data=None, timeout=None):
        self.calls.append({"url": url, "data": data, "timeout": timeout})
        return FakeResponse(self.text)


# --- parsing ---------------------------------------------------------------


def test_parse_converts_tenths_of_mm_to_mm():
    df = parse_daily_csv(RESPONSE)
    assert df["precipitation_mm"].tolist() == [0.0, 0.0, 7.1]
    assert df["makkink_mm"].tolist() == [0.1, 0.3, 1.2]


def test_parse_reads_minus_one_precipitation_as_dry_not_missing():
    # -1 is KNMI's "below 0.05 mm" code; a -0.1 mm day would be nonsense.
    df = parse_daily_csv(RESPONSE)
    assert df.loc[1, "precipitation_mm"] == 0.0
    assert df["precipitation_mm"].notna().all()


def test_parse_returns_expected_columns_and_dtypes():
    df = parse_daily_csv(RESPONSE)
    assert list(df.columns) == ["station", "date", "precipitation_mm", "makkink_mm"]
    assert isinstance(df["date"].dtype, type(pd.api.types.pandas_dtype("datetime64[ns]")))
    assert df["precipitation_mm"].dtype == "float64"
    assert df["station"].tolist() == [DEFAULT_STATION] * 3


def test_parse_blank_field_is_missing_not_zero():
    text = RESPONSE + "  249,20200104,     ,    8\n"
    df = parse_daily_csv(text)
    row = df[df["date"] == pd.Timestamp("2020-01-04")].iloc[0]
    assert pd.isna(row["precipitation_mm"])
    assert row["makkink_mm"] == 0.8


def test_parse_comment_only_response_is_empty_not_an_error():
    df = parse_daily_csv(COMMENTS_ONLY)
    assert df.empty
    assert list(df.columns) == ["station", "date", "precipitation_mm", "makkink_mm"]


def test_parse_deduplicates_on_station_and_date_keeping_last():
    df = parse_daily_csv(RESPONSE + "  249,20200103,  999,   99\n")
    assert len(df) == 3
    assert df.loc[2, "precipitation_mm"] == 99.9


def test_parse_handles_reordered_variables_via_the_header():
    # KNMI echoes its own column order, so the header drives the mapping.
    text = "# STN,YYYYMMDD, EV24,   RH\n  249,20200101,    1,   71\n"
    df = parse_daily_csv(text)
    assert df.loc[0, "makkink_mm"] == 0.1
    assert df.loc[0, "precipitation_mm"] == 7.1


def test_parse_without_header_comment_raises():
    with pytest.raises(ValueError, match="STN"):
        parse_daily_csv("249,20200101,0,1\n")


# --- window planning -------------------------------------------------------


def test_first_run_spans_the_whole_backlog():
    w = plan_window(None, dt.date(2026, 10, 13), dt.date(2020, 1, 1))
    assert (w.start, w.end) == (dt.date(2020, 1, 1), dt.date(2026, 10, 13))
    assert not w.is_empty


def test_incremental_run_starts_after_the_watermark_minus_lookback():
    w = plan_window(dt.date(2026, 10, 11), dt.date(2026, 10, 13), dt.date(2020, 1, 1))
    assert (w.start, w.end) == (dt.date(2026, 10, 10), dt.date(2026, 10, 13))


def test_no_lookback_starts_the_day_after_the_watermark():
    w = plan_window(
        dt.date(2026, 10, 11), dt.date(2026, 10, 13), dt.date(2020, 1, 1), lookback_days=0
    )
    assert w.start == dt.date(2026, 10, 12)


def test_caught_up_window_is_empty():
    today = dt.date(2026, 10, 13)
    w = plan_window(today, today, dt.date(2020, 1, 1), lookback_days=0)
    assert w.is_empty


def test_lookback_never_reaches_before_the_backlog_start():
    backlog = dt.date(2020, 1, 1)
    w = plan_window(backlog, dt.date(2026, 10, 13), backlog, lookback_days=30)
    assert w.start == backlog


def test_negative_lookback_is_rejected():
    with pytest.raises(ValueError, match="lookback_days"):
        plan_window(dt.date(2026, 1, 1), dt.date(2026, 1, 2), dt.date(2020, 1, 1), -1)


# --- fetching --------------------------------------------------------------


def test_fetch_sends_no_request_for_an_empty_window():
    session = FakeSession()
    window = FetchWindow(start=dt.date(2026, 10, 14), end=dt.date(2026, 10, 13))
    df = fetch_daily(window, session=session)
    assert session.calls == []  # the whole point: no needless API traffic
    assert df.empty


def test_fetch_posts_the_documented_parameters():
    session = FakeSession()
    window = FetchWindow(start=dt.date(2020, 1, 1), end=dt.date(2020, 1, 3))
    fetch_daily(window, station=235, session=session)
    sent = session.calls[0]["data"]
    assert sent["start"] == "20200101"
    assert sent["end"] == "20200103"
    assert sent["stns"] == "235"
    assert set(sent["vars"].split(":")) == {"RH", "EV24"}


def test_fetch_returns_the_parsed_frame():
    df = fetch_daily(
        FetchWindow(start=dt.date(2020, 1, 1), end=dt.date(2020, 1, 3)),
        session=FakeSession(),
    )
    assert len(df) == 3
    assert df["precipitation_mm"].tolist() == [0.0, 0.0, 7.1]
