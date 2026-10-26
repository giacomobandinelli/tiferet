"""Tests for the silver stores and their holdout partition (decisions 0002, 0010)."""

from pathlib import Path

import pandas as pd
import pytest

from tiferet.data.store import HOLDOUT_START, InMemoryStore, ParquetStore, UnknownSeriesError

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "silver"
DEV, SEALED = FIXTURES / "dev", FIXTURES / "sealed"
SERIES = "synthetic_cpi"


def test_parquet_development_partition() -> None:
    frame = ParquetStore(DEV, SEALED).read(SERIES, include_holdout=False)
    assert len(frame) == 3
    assert (frame["known_at"] < HOLDOUT_START).all()


def test_parquet_both_partitions() -> None:
    frame = ParquetStore(DEV, SEALED).read(SERIES, include_holdout=True)
    assert len(frame) == 4
    assert (frame["known_at"] >= HOLDOUT_START).sum() == 1


def test_sealed_read_never_opens_the_holdout_root(tmp_path: Path) -> None:
    missing = tmp_path / "absent"
    frame = ParquetStore(DEV, missing).read(SERIES, include_holdout=False)
    assert len(frame) == 3
    assert not missing.exists()


def test_unknown_series_is_refused() -> None:
    with pytest.raises(UnknownSeriesError):
        ParquetStore(DEV, SEALED).read("no_such_series", include_holdout=True)


@pytest.mark.parametrize("series", ["../dev/synthetic_cpi", "Synthetic", "a b", ""])
def test_series_id_that_is_not_an_identifier_is_refused(series: str) -> None:
    with pytest.raises(ValueError, match="invalid series id"):
        ParquetStore(DEV, SEALED).read(series, include_holdout=False)


def test_misfiled_holdout_row_is_refused(tmp_path: Path) -> None:
    # The sealed fixture placed in the development root must not be read as development data.
    store = ParquetStore(SEALED, tmp_path)
    with pytest.raises(ValueError, match="other partition"):
        store.read(SERIES, include_holdout=False)


def test_file_of_another_series_is_refused(tmp_path: Path) -> None:
    (tmp_path / "other.parquet").write_bytes((DEV / f"{SERIES}.parquet").read_bytes())
    with pytest.raises(ValueError, match="another series"):
        ParquetStore(tmp_path, tmp_path / "absent").read("other", include_holdout=False)


def berlin_rows(*known: str) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "series_id": "x",
            "obs_date": pd.Timestamp("2022-12-01"),
            "known_at": pd.to_datetime(list(known)).tz_localize("Europe/Berlin"),
            "value": range(len(known)),
        }
    ).astype({"value": float})


def test_in_memory_store_splits_on_the_berlin_boundary() -> None:
    store = InMemoryStore(berlin_rows("2022-12-31 23:59:59", "2023-01-01 00:00:00"))
    assert len(store.read("x", include_holdout=False)) == 1
    assert len(store.read("x", include_holdout=True)) == 2


def test_in_memory_store_with_only_holdout_rows_is_unknown_when_sealed() -> None:
    store = InMemoryStore(berlin_rows("2023-01-02 00:00:00"))
    with pytest.raises(UnknownSeriesError):
        store.read("x", include_holdout=False)
