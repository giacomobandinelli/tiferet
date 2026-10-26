"""Reference trend features (plan §3.3), Python versions.

Defaults are the pre-registered ones: windows count observations of the daily input, so a
200-day moving average spans 200 trading days, the 2-year one 504, and 12-1 momentum skips
the latest 21. Each input declares the observations its feature's first value depends on.
"""

from collections.abc import Mapping

import numpy as np
import pandas as pd

from tiferet.features.registry import register_python
from tiferet.features.view import Frequency, InputSpec, PitSeries, align, decision_dates

PRICE = InputSpec("price", Frequency.DAILY)
CPI = InputSpec("cpi", Frequency.MONTHLY)

SMA_WINDOW = 200
SMA_2Y_WINDOW = 504
MOMENTUM_LOOKBACK = 252
MOMENTUM_SKIP = 21


def _sma(values: pd.Series, window: int) -> pd.Series:
    return values.rolling(window, min_periods=window).mean()


@register_python("sma_200", PRICE.with_warmup(SMA_WINDOW))
def sma_200(inputs: Mapping[str, PitSeries]) -> pd.Series:
    """Trailing simple moving average of the price over 200 observations."""
    price = inputs[PRICE.role]
    return align(_sma(price.values, SMA_WINDOW), price, decision_dates(inputs))


@register_python("sma_504", PRICE.with_warmup(SMA_2Y_WINDOW))
def sma_504(inputs: Mapping[str, PitSeries]) -> pd.Series:
    """Trailing simple moving average of the price over 504 observations, about two years."""
    price = inputs[PRICE.role]
    return align(_sma(price.values, SMA_2Y_WINDOW), price, decision_dates(inputs))


@register_python("ma_cross_200", PRICE.with_warmup(SMA_WINDOW + 1))
def ma_cross_200(inputs: Mapping[str, PitSeries]) -> pd.Series:
    """1.0 where the price moves from one side of its 200-observation average to the other.

    0.0 at every other observation, NaN until the average exists on two observations. A
    price equal to its average is on neither side, so moving onto it is not a cross.
    """
    price = inputs[PRICE.role]
    gap = price.values - _sma(price.values, SMA_WINDOW)
    side = pd.Series(np.sign(gap.to_numpy(dtype=float)), index=gap.index)
    crossed = (side * side.shift(1) < 0).astype(float)
    computed = crossed.where(side.notna() & side.shift(1).notna())
    return align(computed, price, decision_dates(inputs))


@register_python("momentum_12_1", PRICE.with_warmup(MOMENTUM_LOOKBACK + 1))
def momentum_12_1(inputs: Mapping[str, PitSeries]) -> pd.Series:
    """Return from 252 to 21 observations ago: P[t-21] / P[t-252] - 1."""
    price = inputs[PRICE.role]
    values = price.values
    computed = values.shift(MOMENTUM_SKIP) / values.shift(MOMENTUM_LOOKBACK) - 1.0
    return align(computed, price, decision_dates(inputs))


@register_python("real_price", PRICE.with_warmup(1), CPI.with_warmup(1))
def real_price(inputs: Mapping[str, PitSeries]) -> pd.Series:
    """Daily price divided by the monthly CPI, each CPI value used only once it was known."""
    dates = decision_dates(inputs)
    price, cpi = inputs[PRICE.role], inputs[CPI.role]
    return align(price.values, price, dates) / align(cpi.values, cpi, dates)
