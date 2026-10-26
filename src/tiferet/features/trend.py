"""Reference trend features (plan §3.3), Python versions.

Defaults are the pre-registered ones: windows count observations of the daily input, so a
200-day moving average spans 200 trading days and 12-1 momentum skips the latest 21.
"""

from collections.abc import Mapping

import pandas as pd

from tiferet.features.registry import register_python
from tiferet.features.view import Frequency, InputSpec, PitSeries, align, decision_dates

PRICE = InputSpec("price", Frequency.DAILY)
CPI = InputSpec("cpi", Frequency.MONTHLY)

SMA_WINDOW = 200
MOMENTUM_LOOKBACK = 252
MOMENTUM_SKIP = 21


@register_python("sma_200", PRICE)
def sma_200(inputs: Mapping[str, PitSeries]) -> pd.Series:
    """Trailing simple moving average of the price over 200 observations."""
    price = inputs[PRICE.role]
    computed = price.values.rolling(SMA_WINDOW, min_periods=SMA_WINDOW).mean()
    return align(computed, price, decision_dates(inputs))


@register_python("momentum_12_1", PRICE)
def momentum_12_1(inputs: Mapping[str, PitSeries]) -> pd.Series:
    """Return from 252 to 21 observations ago: P[t-21] / P[t-252] - 1."""
    price = inputs[PRICE.role]
    values = price.values
    computed = values.shift(MOMENTUM_SKIP) / values.shift(MOMENTUM_LOOKBACK) - 1.0
    return align(computed, price, decision_dates(inputs))


@register_python("real_price", PRICE, CPI)
def real_price(inputs: Mapping[str, PitSeries]) -> pd.Series:
    """Daily price divided by the monthly CPI, each CPI value used only once it was known."""
    dates = decision_dates(inputs)
    price, cpi = inputs[PRICE.role], inputs[CPI.role]
    return align(price.values, price, dates) / align(cpi.values, cpi, dates)
