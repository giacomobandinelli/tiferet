"""BTC-to-stablecoin ratio (plan §3.3; decision 0017).

BTC market cap / (USDT market cap + USDC market cap), on daily decision dates. Each input is
placed on the decision dates by `align`, so it counts only once it was known. A stablecoin
enters the sum from its first known value; the ratio exists only where BTC and at least one
stablecoin are known and the sum is positive. `ratio_and_count` also gives the number of
stablecoins in the sum on each date. Only the ratio is a registered feature: the count does
not respond to the inputs' values, which the registry's look-ahead test requires of every
feature.
"""

from collections.abc import Mapping

import pandas as pd

from tiferet.features.registry import register_python
from tiferet.features.view import Frequency, InputSpec, PitSeries, align, decision_dates

BTC_CAP = InputSpec("btc_cap", Frequency.DAILY, warmup=1)
STABLECOIN_CAPS = (
    InputSpec("usdt_cap", Frequency.DAILY, warmup=1),
    InputSpec("usdc_cap", Frequency.DAILY, warmup=1),
)
INPUT_SERIES = {
    "btc_cap": "cm_btc_cap_usd",
    "usdt_cap": "defillama_usdt_cap_usd",
    "usdc_cap": "defillama_usdc_cap_usd",
}
"""The silver series each input role reads (config/sources.yaml)."""
RATIO = "ratio"
STABLECOINS = "stablecoins"


def ratio_and_count(inputs: Mapping[str, PitSeries]) -> pd.DataFrame:
    """The ratio and the number of stablecoins in its denominator, by decision date."""
    dates = decision_dates(inputs)
    btc = align(inputs[BTC_CAP.role].values, inputs[BTC_CAP.role], dates)
    coins = pd.concat(
        [align(inputs[s.role].values, inputs[s.role], dates) for s in STABLECOIN_CAPS], axis=1
    )
    count = coins.notna().sum(axis=1).astype(float)
    total = coins.sum(axis=1, min_count=1)
    ratio = (btc / total).where(total > 0)
    return pd.DataFrame({RATIO: ratio, STABLECOINS: count}, index=dates)


@register_python("btc_stablecoin_ratio", BTC_CAP, *STABLECOIN_CAPS)
def btc_stablecoin_ratio(inputs: Mapping[str, PitSeries]) -> pd.Series:
    """BTC market cap / (USDT + USDC market cap), each stablecoin from its first known value."""
    ratio: pd.Series = ratio_and_count(inputs)[RATIO].rename("value")
    return ratio
