"""Calendar arithmetic for the publication rules of the adapters (decision 0013).

Pure functions over series of naive observation dates. A rule turns each observation date
into the UTC moment its value is taken to be known: the end of the observation period plus
a lag, at a local time of day.
"""

from datetime import date, time, timedelta
from enum import StrEnum
from functools import cache
from zoneinfo import ZoneInfo

import pandas as pd

NEW_YORK = ZoneInfo("America/New_York")
END_OF_DAY = time(23, 59, 59)
TARGET_EXTRA_CLOSING_YEARS = (1999, 2000, 2001)
"""Years in which TARGET was also closed on 31 December."""


class Frequency(StrEnum):
    DAILY = "daily"
    MONTHLY = "monthly"
    QUARTERLY = "quarterly"


def period_end(obs: pd.Series, frequency: Frequency) -> pd.Series:
    """The last day of each observation's period, as a naive midnight."""
    days: pd.Series = obs.dt.normalize()
    match frequency:
        case Frequency.DAILY:
            return days
        case Frequency.MONTHLY:
            return days + pd.offsets.MonthEnd(0)
        case Frequency.QUARTERLY:
            return days + pd.offsets.QuarterEnd(0)


def at_local_time(days: pd.Series, clock: time, tz: ZoneInfo) -> pd.Series:
    """The moment `clock` on each day in `tz`, in UTC."""
    offset = pd.Timedelta(hours=clock.hour, minutes=clock.minute, seconds=clock.second)
    local = days.dt.normalize() + offset
    moments: pd.Series = local.dt.tz_localize(tz).dt.tz_convert("UTC")
    return moments


def end_of_day(days: pd.Series, tz: ZoneInfo) -> pd.Series:
    """23:59:59 on each day in `tz`, in UTC."""
    return at_local_time(days, END_OF_DAY, tz)


def plus_days(days: pd.Series, lag: int) -> pd.Series:
    """Each day moved `lag` calendar days later."""
    if lag < 0:
        raise ValueError(f"a publication lag cannot be negative, got {lag}")
    return days + pd.Timedelta(days=lag)


def known_after_lag(
    obs: pd.Series, frequency: Frequency, lag: int, tz: ZoneInfo = NEW_YORK
) -> pd.Series:
    """23:59:59 in `tz` on the day `lag` days after the end of each observation's period."""
    return end_of_day(plus_days(period_end(obs, frequency), lag), tz)


def easter_sunday(year: int) -> date:
    """Western Easter Sunday (anonymous Gregorian algorithm)."""
    a, b, c = year % 19, year // 100, year % 100
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    j = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * j) // 451
    month, day = divmod(h + j - 7 * m + 114, 31)
    return date(year, month, day + 1)


def target_closing_days(year: int) -> frozenset[date]:
    """Weekday holidays of the TARGET system in `year`.

    New Year, Good Friday, Easter Monday, 1 May, 25 and 26 December, and 31 December in
    1999 to 2001. Treating a day as closed when it was open only delays a `known_at`.
    """
    easter = easter_sunday(year)
    days = {
        date(year, 1, 1),
        easter - timedelta(days=2),
        easter + timedelta(days=1),
        date(year, 5, 1),
        date(year, 12, 25),
        date(year, 12, 26),
    }
    if year in TARGET_EXTRA_CLOSING_YEARS:
        days.add(date(year, 12, 31))
    return frozenset(days)


def is_target_day(day: date) -> bool:
    return day.weekday() < 5 and day not in target_closing_days(day.year)


def add_target_days(day: date, count: int) -> date:
    """The `count`-th TARGET business day after `day`; `day` itself when `count` is 0."""
    if count < 0:
        raise ValueError(f"count cannot be negative, got {count}")
    current = day
    for _ in range(count):
        current += timedelta(days=1)
        while not is_target_day(current):
            current += timedelta(days=1)
    return current


def shift_target_days(days: pd.Series, count: int) -> pd.Series:
    """Each day moved `count` TARGET business days later, as a naive midnight."""
    unique = {d: add_target_days(d, count) for d in days.dt.date.unique()}
    shifted: pd.Series = pd.to_datetime(days.dt.date.map(unique)).astype(days.dtype)
    return shifted


US_SPECIAL_CLOSINGS = frozenset(
    {
        date(1985, 9, 27),
        date(1994, 4, 27),
        date(2001, 9, 11),
        date(2001, 9, 12),
        date(2001, 9, 13),
        date(2001, 9, 14),
        date(2004, 6, 11),
        date(2007, 1, 2),
        date(2012, 10, 29),
        date(2012, 10, 30),
        date(2018, 12, 5),
        date(2025, 1, 9),
    }
)
"""Unscheduled weekday closings of the New York Stock Exchange since 1985: storms, the
September 2001 attacks and national days of mourning."""


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    """The `n`-th `weekday` (Monday 0) of the month; the last one when `n` is -1."""
    if n > 0:
        first = date(year, month, 1)
        return first + timedelta(days=(weekday - first.weekday()) % 7 + 7 * (n - 1))
    following = date(year + month // 12, month % 12 + 1, 1)
    last = following - timedelta(days=1)
    return last - timedelta(days=(last.weekday() - weekday) % 7)


def _observed(day: date) -> date:
    """A Saturday holiday is observed on the Friday before, a Sunday one on the Monday after."""
    if day.weekday() == 5:
        return day - timedelta(days=1)
    if day.weekday() == 6:
        return day + timedelta(days=1)
    return day


def _us_holidays(year: int) -> set[date]:
    fixed = [date(year, 1, 1), date(year, 6, 19), date(year, 7, 4)]
    fixed += [date(year, 11, 11), date(year, 12, 25)]
    return {
        *fixed,
        *(_observed(d) for d in fixed),
        _nth_weekday(year, 1, 0, 3),
        _nth_weekday(year, 2, 0, 3),
        _nth_weekday(year, 5, 0, -1),
        _nth_weekday(year, 9, 0, 1),
        _nth_weekday(year, 10, 0, 2),
        _nth_weekday(year, 11, 3, 4),
        easter_sunday(year) - timedelta(days=2),
    }


@cache
def us_closing_days(year: int) -> frozenset[date]:
    """Weekdays in `year` treated as closed for US-listed funds.

    The US federal holidays (Juneteenth and Martin Luther King Day in every year) on their
    date and their observed date, Good Friday, and the special closings of the New York Stock
    Exchange. The federal holidays that the exchange keeps open, and the years before a
    holiday existed, are treated as closed too: a day wrongly treated as closed only delays a
    `known_at`.
    """
    days = _us_holidays(year - 1) | _us_holidays(year) | _us_holidays(year + 1)
    days |= US_SPECIAL_CLOSINGS
    return frozenset(d for d in days if d.year == year and d.weekday() < 5)


def is_us_day(day: date) -> bool:
    return day.weekday() < 5 and day not in us_closing_days(day.year)


def add_us_days(day: date, count: int) -> date:
    """The `count`-th US business day after `day`; `day` itself when `count` is 0."""
    if count < 0:
        raise ValueError(f"count cannot be negative, got {count}")
    current = day
    for _ in range(count):
        current += timedelta(days=1)
        while not is_us_day(current):
            current += timedelta(days=1)
    return current


def shift_us_days(days: pd.Series, count: int) -> pd.Series:
    """Each day moved `count` US business days later, as a naive midnight."""
    unique = {d: add_us_days(d, count) for d in days.dt.date.unique()}
    shifted: pd.Series = pd.to_datetime(days.dt.date.map(unique)).astype(days.dtype)
    return shifted
