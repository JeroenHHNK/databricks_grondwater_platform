import pandas as pd

from tarso_pipeline.forcing_plot import annual_totals


def daily(start, days, precipitation=1.0, makkink=0.5):
    dates = pd.date_range(start, periods=days, freq="D")
    return pd.DataFrame(
        {
            "date": dates,
            "precipitation_mm": [precipitation] * days,
            "makkink_mm": [makkink] * days,
        }
    )


def test_totals_sum_each_year_in_mm():
    totals = annual_totals(daily("2021-01-01", 365, precipitation=2.0, makkink=1.0))
    assert totals.loc[2021, "precipitation_mm"] == 730.0
    assert totals.loc[2021, "makkink_mm"] == 365.0


def test_full_common_year_is_complete():
    totals = annual_totals(daily("2021-01-01", 365))
    assert totals.loc[2021, "days"] == 365
    assert bool(totals.loc[2021, "complete"])


def test_leap_year_needs_366_days_to_be_complete():
    # 365 days of a leap year is a day short, and must not be called complete.
    short = annual_totals(daily("2020-01-01", 365))
    assert not bool(short.loc[2020, "complete"])

    full = annual_totals(daily("2020-01-01", 366))
    assert bool(full.loc[2020, "complete"])


def test_partial_year_is_incomplete_and_reports_its_last_date():
    totals = annual_totals(daily("2026-01-01", 284))
    assert not bool(totals.loc[2026, "complete"])
    assert totals.loc[2026, "last_date"] == pd.Timestamp("2026-10-11")


def test_years_are_split_apart():
    totals = annual_totals(daily("2020-12-30", 4))  # 2 days in 2020, 2 in 2021
    assert totals.index.tolist() == [2020, 2021]
    assert totals.loc[2020, "days"] == 2
    assert totals.loc[2021, "days"] == 2
    assert not bool(totals.loc[2020, "complete"])


def test_string_dates_are_accepted():
    frame = daily("2021-01-01", 3)
    frame["date"] = frame["date"].astype(str)
    totals = annual_totals(frame)
    assert totals.loc[2021, "days"] == 3


def test_missing_values_do_not_break_the_sum():
    frame = daily("2021-01-01", 3)
    frame.loc[1, "precipitation_mm"] = None
    totals = annual_totals(frame)
    assert totals.loc[2021, "precipitation_mm"] == 2.0
    assert totals.loc[2021, "days"] == 3
