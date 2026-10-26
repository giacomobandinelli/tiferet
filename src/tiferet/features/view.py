"""Point-in-time views of feature inputs and their alignment to decision dates (decision 0010).

A feature reads each input as a `PitSeries`: the values of one series indexed by `obs_date`,
with the `known_at` of each value beside them and the moment `as_of` the view was taken. A
feature computes per observation and then places the result on daily decision dates with
`align`, which shows a value only once it was known. Decision date `d` stands for the
moment `decision_time(d)`, 23:59:59 in Berlin.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Final

import pandas as pd

from tiferet.data.schema import KNOWN_AT, OBS_DATE, VALUE
from tiferet.data.store import BERLIN

END_OF_DAY = pd.Timedelta(hours=23, minutes=59, seconds=59)
TIMESTAMP_UNIT: Final = "us"


class Frequency(StrEnum):
    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"
    QUARTERLY = "quarterly"


@dataclass(frozen=True)
class InputSpec:
    """One input of a feature: the role it plays and the frequency of its observations."""

    role: str
    frequency: Frequency


@dataclass(frozen=True)
class PitSeries:
    """One series as known at `as_of`: values and `known_at`, both indexed by `obs_date`."""

    values: pd.Series
    known_at: pd.Series
    as_of: pd.Timestamp

    def __post_init__(self) -> None:
        index = self.values.index
        if not isinstance(index, pd.DatetimeIndex) or not index.equals(self.known_at.index):
            raise ValueError("values and known_at need the same obs_date DatetimeIndex")
        if not index.is_unique or not index.is_monotonic_increasing:
            raise ValueError("obs_date must be unique and increasing")
        if not isinstance(self.known_at.dtype, pd.DatetimeTZDtype) or self.as_of.tzinfo is None:
            raise ValueError("known_at and as_of must be timezone-aware")
        if (self.known_at > self.as_of).any():
            raise ValueError("a view holds no value known after its as_of")

    @classmethod
    def from_frame(cls, frame: pd.DataFrame, as_of: pd.Timestamp) -> "PitSeries":
        """Build a view from the frame `pit.get` returned for `as_of`."""
        ordered = frame.sort_values(OBS_DATE)
        index = pd.DatetimeIndex(ordered[OBS_DATE], name=OBS_DATE)
        values = pd.Series(ordered[VALUE].to_numpy(dtype=float), index=index, name=VALUE)
        known_at = pd.Series(ordered[KNOWN_AT].array, index=index, name=KNOWN_AT)
        return cls(values, known_at, pd.Timestamp(as_of))

    def available_at(self) -> pd.Series:
        """When a trailing computation at each observation can first be known.

        An observation's result depends on it and every earlier observation, so it is
        available once all of them are known: the running maximum of `known_at`.
        """
        return self.known_at.cummax()


def decision_time(dates: pd.DatetimeIndex) -> pd.DatetimeIndex:
    """The moment each decision date stands for: 23:59:59 Berlin time, in UTC."""
    local = dates.normalize().tz_localize(None) + END_OF_DAY
    return local.tz_localize(BERLIN).tz_convert("UTC").as_unit(TIMESTAMP_UNIT)


def decision_dates(views: Mapping[str, PitSeries]) -> pd.DatetimeIndex:
    """Business days from the first Berlin date on which any input became known to `as_of`.

    The last decision date is the Berlin date of `as_of` (or the business day before it), so
    the value at the last date is the value known at `as_of`. All views share one `as_of`.
    """
    as_ofs = {view.as_of for view in views.values()}
    if len(as_ofs) != 1:
        raise ValueError("the views of one feature must share one as_of")
    stamps = [view.known_at for view in views.values() if not view.known_at.empty]
    if not stamps:
        return pd.DatetimeIndex([], name="date").as_unit(TIMESTAMP_UNIT)
    first = pd.concat(stamps).dt.tz_convert(BERLIN).min().date()
    last = as_ofs.pop().tz_convert(BERLIN).date()
    return pd.bdate_range(first, last, name="date").as_unit(TIMESTAMP_UNIT)


def align(computed: pd.Series, view: PitSeries, dates: pd.DatetimeIndex) -> pd.Series:
    """Place per-observation results on decision dates, forward-filled from availability.

    The value on date `d` is the result of the latest observation available by
    `decision_time(d)`, or NaN before the first one.
    """
    if not computed.index.equals(view.values.index):
        raise ValueError("computed values must be indexed like the view")
    right = pd.DataFrame(
        {
            "at": view.available_at().dt.tz_convert("UTC").dt.as_unit(TIMESTAMP_UNIT).array,
            VALUE: computed.to_numpy(dtype=float),
        }
    ).drop_duplicates("at", keep="last")
    left = pd.DataFrame({"at": decision_time(dates)})
    merged = pd.merge_asof(left, right, on="at", direction="backward")
    return pd.Series(merged[VALUE].to_numpy(dtype=float), index=dates, name=VALUE)
