"""Tests for point-in-time views and their alignment to decision dates (decision 0010)."""

import math

import pandas as pd
import pytest

from tiferet.data.pit import DEVELOPMENT_END
from tiferet.features.view import PitSeries, align, decision_dates, decision_time

AS_OF = pd.Timestamp("2022-03-31 23:59:59", tz="Europe/Berlin")


def view(
    obs: list[str], known: list[str], values: list[float], as_of: pd.Timestamp = AS_OF
) -> PitSeries:
    index = pd.DatetimeIndex(pd.to_datetime(obs), name="obs_date")
    return PitSeries(
        pd.Series(values, index=index, dtype=float),
        pd.Series(pd.to_datetime(known, utc=True), index=index),
        as_of,
    )


def test_decision_time_is_end_of_day_in_berlin() -> None:
    moments = decision_time(pd.DatetimeIndex(["2022-12-31", "2022-07-01"]))
    assert moments[0] == DEVELOPMENT_END
    assert moments[1] == pd.Timestamp("2022-07-01 21:59:59", tz="UTC")  # summer time


def test_decision_dates_run_from_first_known_to_as_of() -> None:
    cpi = view(["2022-01-01"], ["2022-02-15 07:00"], [1.0])
    price = view(["2022-03-01"], ["2022-03-01 17:00"], [2.0])
    dates = decision_dates({"cpi": cpi, "price": price})
    assert dates[0] == pd.Timestamp("2022-02-15")
    assert dates[-1] == pd.Timestamp("2022-03-31")
    assert (dates.dayofweek < 5).all()


def test_decision_dates_end_on_the_business_day_before_a_weekend_as_of() -> None:
    saturday = pd.Timestamp("2022-04-02 12:00", tz="Europe/Berlin")
    price = view(["2022-03-31"], ["2022-03-31 17:00"], [2.0], saturday)
    assert decision_dates({"price": price})[-1] == pd.Timestamp("2022-04-01")


def test_decision_dates_of_empty_views_are_empty() -> None:
    assert decision_dates({"price": view([], [], [])}).empty


def test_views_must_share_as_of() -> None:
    a = view(["2022-03-01"], ["2022-03-01 17:00"], [2.0])
    b = view(["2022-03-01"], ["2022-03-01 17:00"], [2.0], AS_OF + pd.Timedelta(days=1))
    with pytest.raises(ValueError, match="share one as_of"):
        decision_dates({"a": a, "b": b})


def test_view_refuses_values_known_after_as_of() -> None:
    with pytest.raises(ValueError, match="known after"):
        view(["2022-03-01"], ["2022-04-02 00:00"], [1.0])


def test_view_needs_increasing_unique_obs_dates() -> None:
    with pytest.raises(ValueError, match="unique and increasing"):
        view(["2022-03-02", "2022-03-01"], ["2022-03-03", "2022-03-03"], [1.0, 2.0])


def test_align_forward_fills_from_known_at_not_obs_date() -> None:
    cpi = view(["2022-01-01", "2022-02-01"], ["2022-02-15 07:00", "2022-03-15 07:00"], [1.0, 2.0])
    dates = pd.bdate_range("2022-01-03", "2022-03-31")
    aligned = align(cpi.values, cpi, dates)
    assert math.isnan(aligned[pd.Timestamp("2022-02-14")])
    assert aligned[pd.Timestamp("2022-02-15")] == 1.0
    assert aligned[pd.Timestamp("2022-03-14")] == 1.0
    assert aligned[pd.Timestamp("2022-03-15")] == 2.0


def test_align_waits_until_every_earlier_observation_is_known() -> None:
    # The second observation is published before the first; a trailing result at the second
    # depends on the first, so it is available only once the first is known.
    late_first = view(
        ["2022-03-01", "2022-03-02"], ["2022-03-10 07:00", "2022-03-03 07:00"], [1.0, 2.0]
    )
    dates = pd.bdate_range("2022-03-01", "2022-03-11")
    aligned = align(late_first.values, late_first, dates)
    assert aligned[: pd.Timestamp("2022-03-09")].isna().all()
    assert aligned[pd.Timestamp("2022-03-10")] == 2.0


def test_align_needs_results_indexed_like_the_view() -> None:
    price = view(["2022-03-01"], ["2022-03-01 17:00"], [1.0])
    with pytest.raises(ValueError, match="indexed like the view"):
        align(pd.Series([1.0]), price, pd.bdate_range("2022-03-01", "2022-03-02"))
