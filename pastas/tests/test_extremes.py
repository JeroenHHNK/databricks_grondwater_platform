import numpy as np
import pandas as pd
import pytest

from tarso_pipeline.extremes import (
    MIN_COVERAGE,
    annual_extremes,
    block_coverage,
    hydrological_year,
    pool_members,
)


def daily(start, end, seed=0):
    index = pd.date_range(start, end, freq="D")
    return pd.Series(np.random.default_rng(seed).normal(0, 1, len(index)), index=index)


# --- the hydrological year -------------------------------------------------


def test_april_starts_the_year():
    index = pd.to_datetime(["2000-03-31", "2000-04-01", "2000-12-31", "2001-01-01"])
    assert list(hydrological_year(pd.DatetimeIndex(index))) == [1999, 2000, 2000, 2000]


def test_a_winter_is_not_split_across_blocks():
    # The whole point: Dec 2000 and Jan 2001 are one winter and one block. A
    # calendar year would put them in 2000 and 2001, letting one winter supply
    # the maximum of two adjacent blocks.
    winter = pd.to_datetime(["2000-12-15", "2001-01-15", "2001-02-15"])
    labels = hydrological_year(pd.DatetimeIndex(winter))
    assert len(set(labels)) == 1 and labels[0] == 2000


def test_a_summer_drought_stays_where_it_was():
    summer = pd.to_datetime(["2000-07-15", "2000-08-15"])
    assert list(hydrological_year(pd.DatetimeIndex(summer))) == [2000, 2000]


def test_start_month_must_be_a_month():
    with pytest.raises(ValueError, match="start_month"):
        hydrological_year(pd.DatetimeIndex(["2000-01-01"]), start_month=13)


# --- partial blocks --------------------------------------------------------


def test_a_full_year_is_complete():
    coverage = block_coverage(daily("2000-04-01", "2001-03-31"))
    assert coverage.loc[2000] >= MIN_COVERAGE


def test_three_months_is_not_an_annual_extreme():
    # The maximum over three months is not an annual maximum.
    dry, wet = annual_extremes(daily("2000-04-01", "2000-06-30"))
    assert dry.empty and wet.empty


def test_partial_first_and_last_years_are_dropped():
    # 1951-01-01 starts mid hydrological year 1950, which is therefore partial.
    dry, wet = annual_extremes(daily("1951-01-01", "1953-12-31"))
    assert 1950 not in set(wet["year"])
    assert set(wet["year"]) == {1951, 1952}


# --- the extremes themselves -----------------------------------------------


def test_one_minimum_and_one_maximum_per_year():
    dry, wet = annual_extremes(daily("2000-04-01", "2003-03-31"))
    assert len(dry) == len(wet) == 3
    assert sorted(wet["year"]) == [2000, 2001, 2002]


def test_the_index_is_the_date_the_extreme_occurred():
    index = pd.date_range("2000-04-01", "2001-03-31", freq="D")
    values = pd.Series(0.0, index=index)
    values.loc["2000-09-09"] = 5.0
    values.loc["2000-11-11"] = -5.0
    dry, wet = annual_extremes(values)
    assert str(wet.index[0].date()) == "2000-09-09"
    assert str(dry.index[0].date()) == "2000-11-11"
    assert wet["head_max"].iloc[0] == 5.0


def test_empty_input_does_not_raise():
    dry, wet = annual_extremes(pd.Series(dtype="float64"))
    assert dry.empty and wet.empty


# --- pooling ---------------------------------------------------------------


def test_pooling_stacks_members_and_tags_them():
    per_member = {}
    for i in range(1, 4):
        _, wet = annual_extremes(daily("2000-04-01", "2003-03-31", seed=i))
        per_member[f"ens0{i}"] = wet
    pooled = pool_members(per_member)
    assert len(pooled) == 9  # 3 members x 3 years
    assert set(pooled["member"]) == {"ens01", "ens02", "ens03"}


def test_pooling_does_not_join_members_in_time():
    # Each member keeps its own years; nothing is concatenated day by day.
    per_member = {}
    for i in range(1, 3):
        _, wet = annual_extremes(daily("2000-04-01", "2002-03-31", seed=i))
        per_member[f"ens0{i}"] = wet
    pooled = pool_members(per_member)
    assert sorted(pooled["year"]) == [2000, 2000, 2001, 2001]
    assert len(pooled) == 4  # not 2 years of merged daily data


def test_sixteen_members_of_44_years_pool_to_704():
    # The spec's definition of done: 704, not 800 and not 16 440.
    per_member = {}
    for i in range(1, 17):
        index = pd.date_range("1956-01-01", "2000-12-31", freq="D")
        series = pd.Series(np.random.default_rng(i).normal(0, 1, len(index)), index=index)
        _, wet = annual_extremes(series)
        per_member[f"ens{i:02d}"] = wet
    pooled = pool_members(per_member)
    assert len(pooled) == 704
    assert pooled["year"].min() == 1956 and pooled["year"].max() == 1999
    assert pooled["member"].nunique() == 16


def test_pooling_nothing_gives_an_empty_frame():
    assert pool_members({}).empty
    assert pool_members({"ens01": pd.DataFrame()}).empty
