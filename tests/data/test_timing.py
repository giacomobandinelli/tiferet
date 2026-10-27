"""Tests for the calendar arithmetic of the publication rules (decision 0013)."""

from datetime import date, time

import pandas as pd
import pytest

from tiferet.data.store import BERLIN
from tiferet.data.timing import (
    NEW_YORK,
    Frequency,
    add_target_days,
    at_local_time,
    easter_sunday,
    end_of_day,
    is_target_day,
    period_end,
    plus_days,
    shift_target_days,
    target_closing_days,
)


def days(*values: str) -> pd.Series:
    return pd.Series(pd.to_datetime(list(values))).astype("datetime64[us]")


@pytest.mark.parametrize(
    ("frequency", "obs", "end"),
    [
        (Frequency.DAILY, "2022-05-27", "2022-05-27"),
        (Frequency.MONTHLY, "2022-02-01", "2022-02-28"),
        (Frequency.MONTHLY, "2020-02-15", "2020-02-29"),
        (Frequency.MONTHLY, "2022-01-31", "2022-01-31"),
        (Frequency.QUARTERLY, "2022-04-01", "2022-06-30"),
        (Frequency.QUARTERLY, "2022-12-31", "2022-12-31"),
    ],
)
def test_period_end(frequency: Frequency, obs: str, end: str) -> None:
    assert period_end(days(obs), frequency).iloc[0] == pd.Timestamp(end)


def test_end_of_day_follows_daylight_saving_time() -> None:
    moments = end_of_day(days("2022-01-14", "2022-07-14"), NEW_YORK)
    assert list(moments) == [
        pd.Timestamp("2022-01-15 04:59:59", tz="UTC"),
        pd.Timestamp("2022-07-15 03:59:59", tz="UTC"),
    ]


def test_local_time_in_berlin() -> None:
    moments = at_local_time(days("2022-03-25", "2022-03-28"), time(9), BERLIN)
    assert list(moments) == [
        pd.Timestamp("2022-03-25 08:00", tz="UTC"),
        pd.Timestamp("2022-03-28 07:00", tz="UTC"),
    ]


def test_negative_lag_is_refused() -> None:
    with pytest.raises(ValueError, match="negative"):
        plus_days(days("2022-01-01"), -1)


@pytest.mark.parametrize(
    ("year", "easter"),
    [
        (2000, date(2000, 4, 23)),
        (2019, date(2019, 4, 21)),
        (2022, date(2022, 4, 17)),
        (2024, date(2024, 3, 31)),
    ],
)
def test_easter_sunday(year: int, easter: date) -> None:
    assert easter_sunday(year) == easter


def test_target_calendar() -> None:
    closed = target_closing_days(2022)
    assert closed == {
        date(2022, 1, 1),
        date(2022, 4, 15),
        date(2022, 4, 18),
        date(2022, 5, 1),
        date(2022, 12, 25),
        date(2022, 12, 26),
    }
    assert date(1999, 12, 31) in target_closing_days(1999)
    assert date(2002, 12, 31) not in target_closing_days(2002)
    assert not is_target_day(date(2022, 4, 16))
    assert is_target_day(date(2022, 4, 19))


def test_adding_target_days_skips_weekends_and_holidays() -> None:
    assert add_target_days(date(2022, 4, 14), 1) == date(2022, 4, 19)
    assert add_target_days(date(2022, 12, 23), 1) == date(2022, 12, 27)
    assert add_target_days(date(2022, 12, 30), 0) == date(2022, 12, 30)
    shifted = shift_target_days(days("2022-04-14", "2022-12-30"), 1)
    assert list(shifted) == [pd.Timestamp("2022-04-19"), pd.Timestamp("2023-01-02")]
