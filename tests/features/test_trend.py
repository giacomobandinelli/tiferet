"""Tests for the reference trend features (plan §3.3)."""

import math

import duckdb
import numpy as np
import pandas as pd

import tiferet.features  # noqa: F401  (importing the package fills the registry)
from tiferet.data.schema import validate_silver
from tiferet.features import registry
from tiferet.features.view import PitSeries

DATES = pd.bdate_range("2020-01-01", periods=400)
AS_OF = pd.Timestamp("2021-12-31 23:59:59", tz="Europe/Berlin")
CLOSE = pd.Timedelta(hours=17, minutes=30)


def silver(
    series: str, obs: pd.DatetimeIndex, lag: pd.Timedelta, values: np.ndarray
) -> pd.DataFrame:
    known_at = (obs + lag).tz_localize("Europe/Berlin")
    return validate_silver(
        pd.DataFrame({"series_id": series, "obs_date": obs, "known_at": known_at, "value": values})
    )


PRICE = silver("price", DATES, CLOSE, np.arange(1.0, 401.0))
MONTHS = pd.date_range("2019-12-01", periods=17, freq="MS")
CPI = silver("cpi", MONTHS, pd.Timedelta(days=45), 100.0 + np.arange(17.0))


def views(*frames: pd.DataFrame) -> dict[str, PitSeries]:
    return {str(f["series_id"].iloc[0]): PitSeries.from_frame(f, AS_OF) for f in frames}


def run(name: str, *frames: pd.DataFrame) -> pd.Series:
    return registry.compute(registry.python_features()[name], views(*frames))


def test_sma_200() -> None:
    result = run("sma_200", PRICE)
    assert math.isnan(result[DATES[198]])
    assert result[DATES[199]] == 100.5
    assert result[DATES[399]] == 300.5


def test_sma_200_is_forward_filled_to_as_of() -> None:
    result = run("sma_200", PRICE)
    assert result.index[-1] == pd.Timestamp("2021-12-31")
    assert result.iloc[-1] == 300.5


def test_momentum_12_1() -> None:
    result = run("momentum_12_1", PRICE)
    assert math.isnan(result[DATES[251]])
    assert result[DATES[252]] == 232.0 / 1.0 - 1.0
    assert math.isclose(result[DATES[399]], 379.0 / 148.0 - 1.0)


def test_real_price_uses_cpi_only_once_published() -> None:
    result = run("real_price", PRICE, CPI)
    # The January 2020 CPI (101) is published on 15 February 2020; before that only the
    # December 2019 value (100, published 14 January) is known.
    feb_14, feb_17 = pd.Timestamp("2020-02-14"), pd.Timestamp("2020-02-17")
    assert result[feb_14] == PRICE.set_index("obs_date")["value"][feb_14] / 100.0
    assert result[feb_17] == PRICE.set_index("obs_date")["value"][feb_17] / 101.0
    assert math.isnan(result[pd.Timestamp("2020-01-13")])


def test_sql_sma_200_matches_python() -> None:
    walk = 100 * np.exp(np.cumsum(np.random.default_rng(7).normal(0, 0.01, len(DATES))))
    price = silver("price", DATES, CLOSE, walk)
    con = duckdb.connect()
    con.register("price_frame", price)
    relation = con.sql(
        "SELECT 'price' AS input, obs_date::DATE AS obs_date, known_at, value,"
        f" TIMESTAMPTZ '{AS_OF.isoformat()}' AS as_of FROM price_frame"
    )
    sql = registry.compute_sql(registry.sql_features()["sql_sma_200"], relation)
    python = run("sma_200", price)
    pd.testing.assert_series_equal(sql, python, check_names=False, check_freq=False, rtol=1e-12)
    con.close()
