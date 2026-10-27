"""Silver writer: validated, partitioned Parquet files per series (decisions 0010, 0013).

`write_silver` refreshes whole series. Each series' rows are split on `known_at` at the
holdout start, so development and holdout rows never share a file, and written to
`<root>/<series_id>.parquet` and `<holdout_root>/<series_id>.parquet`. Before the first
file is written, every series in the call passes the data-loss guard against the files it
replaces; one refusal writes nothing.
"""

import os
import tempfile
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from tiferet.data.errors import DataLossError
from tiferet.data.schema import OBS_DATE, SERIES_ID, SILVER_SCHEMA, validate_silver
from tiferet.data.store import check_series_id, is_holdout


@dataclass(frozen=True)
class SilverRoots:
    """Where the two partitions of every series are stored."""

    development: Path
    holdout: Path

    def __post_init__(self) -> None:
        if self.development.resolve() == self.holdout.resolve():
            raise ValueError("development and holdout roots must differ")


@dataclass(frozen=True)
class Written:
    """Rows written for one series, per partition."""

    series_id: str
    development: int
    holdout: int


def write_parquet_atomic(frame: pd.DataFrame, path: Path) -> None:
    """Write `frame` to `path` through a temporary file, so readers never see half a file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    os.close(handle)
    try:
        frame.to_parquet(temporary, index=False)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def _existing(series_id: str, roots: SilverRoots) -> pd.DataFrame | None:
    paths = [root / f"{series_id}.parquet" for root in (roots.development, roots.holdout)]
    frames = [validate_silver(pd.read_parquet(path)) for path in paths if path.is_file()]
    return pd.concat(frames, ignore_index=True) if frames else None


def check_no_data_loss(
    series_id: str, new: pd.DataFrame, existing: pd.DataFrame | None, max_row_loss: float
) -> None:
    """Refuse a refresh that is empty, drops observation dates or loses too many rows.

    Revised values are allowed. Messages give counts only, never values or dates.
    """
    if new.empty:
        raise DataLossError(series_id, "the new download holds no rows")
    if existing is None:
        return
    lost_dates = set(existing[OBS_DATE]) - set(new[OBS_DATE])
    if lost_dates:
        raise DataLossError(
            series_id, f"{len(lost_dates)} observation dates in the stored series are missing"
        )
    floor = len(existing) * (1 - max_row_loss)
    if len(new) < floor:
        raise DataLossError(
            series_id,
            f"{len(new)} rows would replace {len(existing)}, "
            f"more than the tolerated loss of {max_row_loss:.2%}",
        )


def split_by_series(frame: pd.DataFrame, series_ids: Iterable[str]) -> dict[str, pd.DataFrame]:
    """The rows of each series in `series_ids`, empty for a series without rows."""
    groups = {str(key): rows for key, rows in frame.groupby(SERIES_ID, sort=False)}
    return {s: groups.get(s, frame.iloc[0:0]) for s in series_ids}


def _write_partition(rows: pd.DataFrame, path: Path) -> None:
    if rows.empty:
        path.unlink(missing_ok=True)
    else:
        write_parquet_atomic(rows.reset_index(drop=True), path)


def write_silver(
    frame: pd.DataFrame,
    roots: SilverRoots,
    *,
    expected: Iterable[str],
    max_row_loss: float,
) -> list[Written]:
    """Replace the stored files of every series in `expected` with its rows in `frame`.

    A series in `expected` with no rows in `frame` is refused as empty; a row of a series not
    in `expected` is refused as unexpected.
    """
    validated = validate_silver(frame, SILVER_SCHEMA)
    series_ids = [check_series_id(s) for s in dict.fromkeys(expected)]
    unexpected = set(validated[SERIES_ID]) - set(series_ids)
    if unexpected:
        raise ValueError(f"rows of unexpected series: {sorted(unexpected)}")
    groups = split_by_series(validated, series_ids)
    for series_id, rows in groups.items():
        check_no_data_loss(series_id, rows, _existing(series_id, roots), max_row_loss)
    written = []
    for series_id, rows in groups.items():
        holdout = is_holdout(rows)
        _write_partition(rows[~holdout], roots.development / f"{series_id}.parquet")
        _write_partition(rows[holdout], roots.holdout / f"{series_id}.parquet")
        written.append(Written(series_id, int((~holdout).sum()), int(holdout.sum())))
    return written
