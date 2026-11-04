"""Tests for the BTC-to-stablecoin ratio (plan §3.3; decision 0017)."""

import math

import numpy as np
import pandas as pd

import tiferet.features  # noqa: F401  (importing the package fills the registry)
from tiferet.data import pit
from tiferet.data.schema import validate_silver
from tiferet.data.store import BERLIN, InMemoryStore
from tiferet.features import registry
from tiferet.features.crypto import INPUT_SERIES, RATIO, STABLECOINS, ratio_and_count
from tiferet.features.view import PitSeries

DAYS = pd.date_range("2022-01-01", "2022-06-30", freq="D")
USDC_FROM = pd.Timestamp("2022-03-15")
LAG = pd.Timedelta(days=2)  # known at 00:00 UTC two days after the UTC day


def silver(series: str, obs: pd.DatetimeIndex, values: np.ndarray) -> pd.DataFrame:
    known_at = (obs + LAG).tz_localize("UTC")
    frame = pd.DataFrame({"series_id": series, "obs_date": obs, "known_at": known_at})
    return validate_silver(frame.assign(value=values))


BTC = silver("btc_cap", DAYS, 500.0 + np.arange(len(DAYS)))
USDT = silver("usdt_cap", DAYS, np.full(len(DAYS), 80.0))
USDC_DAYS = DAYS[DAYS >= USDC_FROM]
USDC = silver("usdc_cap", USDC_DAYS, np.full(len(USDC_DAYS), 20.0))
STORE = InMemoryStore(pd.concat([BTC, USDT, USDC], ignore_index=True))
USDC_KNOWN = pd.Timestamp(USDC["known_at"].iloc[0])


def views(as_of: pd.Timestamp) -> dict[str, PitSeries]:
    def read(role: str) -> pd.DataFrame:
        return pit.get(role, as_of, mode=pit.Mode.RESEARCH, store=STORE, env={})

    return {role: PitSeries.from_frame(read(role), as_of) for role in INPUT_SERIES}


def berlin(moment: str) -> pd.Timestamp:
    return pd.Timestamp(moment, tz=BERLIN)


def test_inputs_map_to_the_adapter_series() -> None:
    feature = registry.python_features()["btc_stablecoin_ratio"]
    assert feature.roles == set(INPUT_SERIES)
    assert INPUT_SERIES == {
        "btc_cap": "cm_btc_cap_usd",
        "usdt_cap": "defillama_usdt_cap_usd",
        "usdc_cap": "defillama_usdc_cap_usd",
    }


def test_usdc_enters_the_sum_on_the_date_it_became_known() -> None:
    result = ratio_and_count(views(berlin("2022-06-30 23:59:59")))
    # USDC for 15 March is known at 00:00 UTC on 17 March, 01:00 in Berlin.
    before, entry = pd.Timestamp("2022-03-16"), pd.Timestamp("2022-03-17")
    assert result.loc[before, STABLECOINS] == 1.0
    assert result.loc[entry, STABLECOINS] == 2.0
    assert (result.loc[:before, STABLECOINS].dropna() <= 1.0).all()
    assert (result.loc[entry:, STABLECOINS] == 2.0).all()
    # On 16 March the latest known BTC value is that of 14 March, observation 72.
    assert result.loc[before, RATIO] == (500.0 + 72) / 80.0
    assert result.loc[entry, RATIO] == (500.0 + 73) / 100.0


def test_a_view_before_usdc_was_known_ignores_it() -> None:
    full = ratio_and_count(views(berlin("2022-06-30 23:59:59")))
    moment = USDC_KNOWN - pd.Timedelta(microseconds=1)
    early = ratio_and_count(views(moment))
    # The view's last date stands for its as_of, before USDC and that day's BTC were known.
    assert early[STABLECOINS].iloc[-1] == 1.0
    assert early[RATIO].iloc[-1] == (500.0 + 72) / 80.0
    earlier = early.index[:-1]
    pd.testing.assert_frame_equal(early.loc[earlier], full.loc[earlier])


def test_the_registered_feature_is_the_ratio() -> None:
    feature = registry.python_features()["btc_stablecoin_ratio"]
    inputs = views(berlin("2022-06-30 23:59:59"))
    result = registry.compute(feature, inputs)
    pd.testing.assert_series_equal(
        result, ratio_and_count(inputs)[RATIO].rename("value"), check_names=False
    )


def test_no_ratio_without_btc_or_without_any_stablecoin() -> None:
    late = DAYS[pd.Timestamp("2022-02-01") <= DAYS]
    late_btc = silver("btc_cap", late, np.full(len(late), 1.0))
    store = InMemoryStore(pd.concat([late_btc, USDT, USDC], ignore_index=True))
    as_of = berlin("2022-06-30 23:59:59")
    inputs = {
        role: PitSeries.from_frame(
            pit.get(role, as_of, mode=pit.Mode.RESEARCH, store=store, env={}), as_of
        )
        for role in INPUT_SERIES
    }
    result = ratio_and_count(inputs)
    assert math.isnan(result[RATIO][pd.Timestamp("2022-01-31")])
    assert result.loc[pd.Timestamp("2022-01-31"), STABLECOINS] == 1.0
    no_coins = {
        "btc_cap": inputs["btc_cap"],
        "usdt_cap": PitSeries.from_frame(USDT.iloc[0:0], as_of),
        "usdc_cap": PitSeries.from_frame(USDC.iloc[0:0], as_of),
    }
    empty = ratio_and_count(no_coins)
    assert empty[RATIO].isna().all() and (empty[STABLECOINS] == 0.0).all()


def test_no_ratio_when_the_stablecoin_sum_is_zero() -> None:
    as_of = berlin("2022-07-31 23:59:59")
    zero = silver("usdt_cap", DAYS, np.zeros(len(DAYS)))
    inputs = {
        "btc_cap": PitSeries.from_frame(BTC, as_of),
        "usdt_cap": PitSeries.from_frame(zero, as_of),
        "usdc_cap": PitSeries.from_frame(USDC.iloc[0:0], as_of),
    }
    result = ratio_and_count(inputs)
    assert result[RATIO].isna().all()
    assert result[STABLECOINS].max() == 1.0
