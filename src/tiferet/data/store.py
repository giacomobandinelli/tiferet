"""Storage of silver point-in-time series (plan §4, §5.2; decisions 0002, 0010).

Each series is stored in two partitions. The development partition holds every version
that became known before the holdout starts; the holdout partition holds every version
that became known from then on. The split is on `known_at`, not `obs_date`: a December 2022
value published in January 2023 is holdout. A store opens the holdout partition only when a
caller asks for it, so a sealed research read never touches it.

Stores do I/O and nothing else; the point-in-time rule is applied in `pit.py`.
"""

import re
from pathlib import Path
from typing import Protocol
from zoneinfo import ZoneInfo

import pandas as pd

from tiferet.data.schema import (
    KNOWN_AT,
    SERIES_ID,
    SERIES_ID_PATTERN,
    validate_silver,
)

BERLIN = ZoneInfo("Europe/Berlin")
HOLDOUT_START = pd.Timestamp("2023-01-01 00:00:00", tz=BERLIN)
"""First moment of the holdout: a version known at or after it is in the holdout partition."""

DATA_DIR = Path(__file__).resolve().parents[3] / "data"
SILVER_DIR = DATA_DIR / "silver"
HOLDOUT_SILVER_DIR = DATA_DIR / "holdout" / "silver"


class UnknownSeriesError(LookupError):
    """Raised when a store holds no data for a series."""


class Store(Protocol):
    """Source of silver rows for one series, every version included."""

    def read(self, series_id: str, *, include_holdout: bool) -> pd.DataFrame:
        """Return the validated silver rows of `series_id`.

        The holdout partition is read only when `include_holdout` is true.
        """
        ...


def check_series_id(series_id: str) -> str:
    """Refuse a series name that is not a plain identifier, such as one holding a path."""
    if not re.fullmatch(SERIES_ID_PATTERN, series_id):
        raise ValueError(f"invalid series id: {series_id!r}")
    return series_id


def is_holdout(frame: pd.DataFrame) -> pd.Series:
    """True for each row whose version became known at or after the holdout start."""
    return frame[KNOWN_AT] >= HOLDOUT_START


class InMemoryStore:
    """A store over a silver frame held in memory, partitioned like the stored data."""

    def __init__(self, frame: pd.DataFrame) -> None:
        validated = validate_silver(frame)
        holdout = is_holdout(validated)
        self._development = validated[~holdout]
        self._holdout = validated[holdout]

    def read(self, series_id: str, *, include_holdout: bool) -> pd.DataFrame:
        check_series_id(series_id)
        parts = [self._development, self._holdout] if include_holdout else [self._development]
        rows = [part[part[SERIES_ID] == series_id] for part in parts]
        if all(part.empty for part in rows):
            raise UnknownSeriesError(series_id)
        return validate_silver(pd.concat(rows, ignore_index=True))


class ParquetStore:
    """A store over one Parquet file per series and partition.

    Files are `<root>/<series_id>.parquet` and `<holdout_root>/<series_id>.parquet`. Each file
    must hold only rows of its own partition, so a misfiled holdout row is refused.
    """

    def __init__(self, root: Path = SILVER_DIR, holdout_root: Path = HOLDOUT_SILVER_DIR) -> None:
        self._root = root
        self._holdout_root = holdout_root

    def read(self, series_id: str, *, include_holdout: bool) -> pd.DataFrame:
        check_series_id(series_id)
        development = self._read_partition(self._root, series_id, holdout=False)
        parts = [development]
        if include_holdout:
            parts.append(self._read_partition(self._holdout_root, series_id, holdout=True))
        if all(part is None for part in parts):
            raise UnknownSeriesError(series_id)
        frames = [part for part in parts if part is not None]
        return validate_silver(pd.concat(frames, ignore_index=True))

    @staticmethod
    def _read_partition(root: Path, series_id: str, *, holdout: bool) -> pd.DataFrame | None:
        path = root / f"{series_id}.parquet"
        if not path.is_file():
            return None
        frame = validate_silver(pd.read_parquet(path))
        if not (is_holdout(frame) == holdout).all():
            raise ValueError(f"{path} holds rows of the other partition")
        if not (frame[SERIES_ID] == series_id).all():
            raise ValueError(f"{path} holds rows of another series")
        return frame
