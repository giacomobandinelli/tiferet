"""Pieces shared by the source adapters (decision 0013).

An adapter's `collect` downloads its configured items, parses each one strictly and stamps
the rows with `known_at` and `vintage_kind`, returning a `Batch`. `ingest` then writes the
silver tables and, only after they are written, appends the bronze manifest, so a format
error or a data-loss refusal leaves both untouched.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import numpy as np
import pandas as pd

from tiferet.data import bronze
from tiferet.data.fetch import Download, Fetcher
from tiferet.data.schema import (
    KNOWN_AT,
    KNOWN_AT_DTYPE,
    OBS_DATE,
    SERIES_ID,
    VALUE,
    VINTAGE_KIND,
    empty_silver,
    validate_silver,
)
from tiferet.data.silver import SilverRoots, Written, split_by_series, write_silver
from tiferet.data.source_config import Sources
from tiferet.data.store import is_holdout


def stamped(
    series_id: str,
    obs_date: pd.Series,
    known_at: pd.Series,
    value: pd.Series,
    vintage_kind: str | Sequence[str] | np.ndarray,
) -> pd.DataFrame:
    """A silver frame for one series from aligned columns."""
    frame = pd.DataFrame(
        {
            SERIES_ID: pd.Series(series_id, index=obs_date.index, dtype=str),
            OBS_DATE: obs_date.astype("datetime64[us]"),
            KNOWN_AT: known_at.astype(KNOWN_AT_DTYPE),
            VALUE: value.astype(float),
            VINTAGE_KIND: pd.Series(vintage_kind, index=obs_date.index, dtype=str),
        }
    )
    return frame.reset_index(drop=True)


def combine(frames: Sequence[pd.DataFrame]) -> pd.DataFrame:
    """Concatenate stamped frames; an empty list gives an empty frame with the columns."""
    if not frames:
        return empty_silver().assign(**{VINTAGE_KIND: pd.Series(dtype=str)})
    return pd.concat(frames, ignore_index=True)


@dataclass(frozen=True)
class Batch:
    """What one adapter run downloaded and the silver rows it stamped from it."""

    source: str
    downloads: tuple[Download, ...]
    silver: pd.DataFrame
    expected: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "silver", validate_silver(self.silver))


class Collect(Protocol):
    """Signature of an adapter's `collect(fetcher, sources, *, smoke)`."""

    def __call__(self, fetcher: Fetcher, sources: Sources, *, smoke: bool) -> Batch: ...


def smoke_lines(batch: Batch) -> list[str]:
    """One line of counts per expected series; never a value."""
    total = sum(len(d.content) for d in batch.downloads)
    lines = [f"{batch.source}: {len(batch.downloads)} files, {total} bytes"]
    for series_id, rows in split_by_series(batch.silver, batch.expected).items():
        sealed = int(is_holdout(rows).sum())
        lines.append(
            f"{series_id}: {len(rows)} rows, {len(rows) - sealed} development, {sealed} sealed"
        )
    return lines


def ingest(
    batch: Batch, roots: SilverRoots, bronze_root: Path, max_row_loss: float
) -> list[Written]:
    """Write the silver tables, then record the downloads in the manifest."""
    written = write_silver(batch.silver, roots, expected=batch.expected, max_row_loss=max_row_loss)
    bronze.record(batch.source, batch.downloads, bronze_root)
    return written
