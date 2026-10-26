"""Point-in-time accessor: the only way Python code reads stamped data (plan §8.1, §10.1).

`get(series, as_of, ...)` returns, for each observation period, the latest version whose
`known_at` is at or before `as_of`, and never a version known later. Every caller declares a
mode (decision 0002). Research mode refuses any `as_of` after the end of the development
period and does not read the holdout partition, unless the holdout is unsealed through the
flag named in decision 0008. Semantics are recorded in decision 0010.
"""

import os
from collections.abc import Mapping
from datetime import date, datetime
from enum import StrEnum

import pandas as pd

from tiferet.data.schema import KNOWN_AT, OBS_DATE, PIT_SCHEMA, validate_silver
from tiferet.data.store import BERLIN, Store

UNSEAL_FLAG = "TIFERET_UNSEAL_HOLDOUT"
DEVELOPMENT_END = pd.Timestamp("2022-12-31 23:59:59", tz=BERLIN)
"""Last `as_of` research mode accepts while the holdout is sealed."""


class Mode(StrEnum):
    """Who is reading (decision 0002). There is no default."""

    RESEARCH = "research"
    """Backtests, optimization and model selection: development data only while sealed."""
    PRODUCTION = "production"
    """Nightly ingestion, data-quality pages and, after the freeze, the live weights."""


class PitError(ValueError):
    """Base class of point-in-time access errors."""


class NaiveTimestampError(PitError):
    """Raised when `as_of` has no time zone, so the moment it names is ambiguous."""


class HoldoutSealedError(PitError):
    """Raised when research mode asks for a moment after the development period."""


def holdout_unsealed(env: Mapping[str, str] = os.environ) -> bool:
    """True only when the unseal flag in `env` is exactly "1" (decision 0008)."""
    return env.get(UNSEAL_FLAG) == "1"


def check_as_of(
    as_of: datetime, mode: Mode, env: Mapping[str, str] | None = None
) -> tuple[pd.Timestamp, bool]:
    """Validate `as_of` for `mode` and return it in UTC, with whether to read the holdout.

    Shared by `get` and the SQL helper so both apply one rule.
    """
    if not isinstance(mode, Mode):
        raise TypeError(f"mode must be a Mode, got {mode!r}")
    stamp = pd.Timestamp(as_of)
    if stamp.tzinfo is None:
        raise NaiveTimestampError(f"as_of must be timezone-aware, got {as_of!r}")
    if mode is Mode.PRODUCTION:
        return stamp.tz_convert("UTC"), True
    unsealed = holdout_unsealed(os.environ if env is None else env)
    if stamp > DEVELOPMENT_END and not unsealed:
        raise HoldoutSealedError(
            f"research mode refuses as_of {stamp.isoformat()} after {DEVELOPMENT_END.isoformat()}"
        )
    return stamp.tz_convert("UTC"), unsealed


def latest_known(rows: pd.DataFrame, as_of: pd.Timestamp) -> pd.DataFrame:
    """For each observation, the latest version with `known_at <= as_of`, sorted by period."""
    known = rows[rows[KNOWN_AT] <= as_of]
    latest = known.sort_values([OBS_DATE, KNOWN_AT]).drop_duplicates(OBS_DATE, keep="last")
    return latest.reset_index(drop=True)


def get(
    series: str,
    as_of: datetime,
    start: date | None = None,
    *,
    mode: Mode,
    store: Store,
    env: Mapping[str, str] | None = None,
) -> pd.DataFrame:
    """Return `series` as it was known at `as_of`.

    The frame has one row per `obs_date` from `start` on (inclusive), holding the latest
    version known at `as_of`, sorted by `obs_date`; it may be empty. `env` stands in for the
    process environment when deciding whether the holdout is unsealed.
    """
    stamp, include_holdout = check_as_of(as_of, mode, env)
    rows = store.read(series, include_holdout=include_holdout)
    if start is not None:
        rows = rows[rows[OBS_DATE] >= pd.Timestamp(start)]
    return validate_silver(latest_known(rows, stamp), PIT_SCHEMA)
