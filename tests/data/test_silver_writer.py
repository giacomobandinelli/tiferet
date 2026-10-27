"""Tests for the silver writer: partitioning and the data-loss guard (decisions 0010, 0013)."""

from pathlib import Path

import pandas as pd
import pytest

from data.adapter_support import silver_roots, snapshot
from tiferet.data.errors import DataLossError
from tiferet.data.schema import SilverSchemaError
from tiferet.data.silver import SilverRoots, write_silver
from tiferet.data.store import BERLIN, ParquetStore


def rows(series: str, versions: list[tuple[str, str, float]]) -> pd.DataFrame:
    obs, known, value = zip(*versions, strict=True)
    return pd.DataFrame(
        {
            "series_id": series,
            "obs_date": pd.to_datetime(list(obs)),
            "known_at": pd.to_datetime(list(known)).tz_localize(BERLIN),
            "value": list(value),
            "vintage_kind": "actual",
        }
    )


MONTHS = [(f"2022-{m:02d}-01", f"2022-{m:02d}-15 08:00", float(m)) for m in range(1, 11)]


def write(frame: pd.DataFrame, roots: SilverRoots, *series: str, loss: float = 0.0) -> None:
    write_silver(
        frame, roots, expected=series or tuple(frame["series_id"].unique()), max_row_loss=loss
    )


def test_rows_split_on_the_berlin_boundary(tmp_path: Path) -> None:
    roots = silver_roots(tmp_path)
    last, first = "2022-12-31 23:59:59", "2023-01-01 00:00:00"
    frame = rows("x", [("2022-12-01", last, 1.0), ("2022-12-01", first, 2.0)])
    [written] = write_silver(frame, roots, expected=["x"], max_row_loss=0.0)
    assert (written.development, written.holdout) == (1, 1)
    store = ParquetStore(roots.development, roots.holdout)
    assert list(store.read("x", include_holdout=False)["value"]) == [1.0]
    assert list(store.read("x", include_holdout=True)["value"]) == [1.0, 2.0]
    assert list(store.read("x", include_holdout=False)["vintage_kind"]) == ["actual"]


def test_stale_holdout_file_is_removed_when_a_partition_empties(tmp_path: Path) -> None:
    roots = silver_roots(tmp_path)
    write(rows("x", [("2022-12-01", "2023-01-02 08:00", 1.0)]), roots)
    assert (roots.holdout / "x.parquet").is_file()
    revised = rows("x", [("2022-12-01", "2022-12-20 08:00", 1.5)])
    write(revised, roots)
    assert not (roots.holdout / "x.parquet").exists()
    assert (roots.development / "x.parquet").is_file()


def test_invalid_frame_and_unexpected_series_are_refused(tmp_path: Path) -> None:
    roots = silver_roots(tmp_path)
    bad = rows("x", MONTHS).assign(vintage_kind="guessed")
    with pytest.raises(SilverSchemaError):
        write(bad, roots)
    with pytest.raises(ValueError, match="unexpected series"):
        write_silver(rows("y", MONTHS), roots, expected=["x"], max_row_loss=0.0)
    assert not tmp_path.joinpath("silver").exists()


def test_roots_must_differ(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="must differ"):
        SilverRoots(tmp_path, tmp_path)


def test_first_write_is_accepted(tmp_path: Path) -> None:
    write(rows("x", MONTHS), silver_roots(tmp_path))
    assert (tmp_path / "silver" / "x.parquet").is_file()


def test_revised_values_are_accepted(tmp_path: Path) -> None:
    roots = silver_roots(tmp_path)
    write(rows("x", MONTHS), roots)
    write(rows("x", [(o, k, v + 0.5) for o, k, v in MONTHS]), roots)
    stored = pd.read_parquet(roots.development / "x.parquet")
    assert stored["value"].iloc[0] == 1.5


def test_empty_download_is_refused(tmp_path: Path) -> None:
    roots = silver_roots(tmp_path)
    write(rows("x", MONTHS), roots)
    before = snapshot(tmp_path)
    with pytest.raises(DataLossError, match="x: the new download holds no rows"):
        write(rows("y", MONTHS), roots, "x", "y")
    assert snapshot(tmp_path) == before


def test_lost_observation_date_is_refused_within_the_row_tolerance(tmp_path: Path) -> None:
    roots = silver_roots(tmp_path)
    extra = [("2022-03-01", "2022-04-20 08:00", 3.1)]
    write(rows("x", MONTHS + extra), roots)
    before = snapshot(tmp_path)
    # One row of eleven is within a 50% tolerance, but the date 2022-10-01 disappears.
    with pytest.raises(DataLossError, match="1 observation dates"):
        write(rows("x", MONTHS[:-1] + extra), roots, loss=0.5)
    assert snapshot(tmp_path) == before


def test_row_loss_beyond_the_tolerance_is_refused(tmp_path: Path) -> None:
    roots = silver_roots(tmp_path)
    revisions = [(o, k.replace("-15 ", "-25 "), v + 0.1) for o, k, v in MONTHS]
    write(rows("x", MONTHS + revisions), roots)
    before = snapshot(tmp_path)
    with pytest.raises(DataLossError, match="10 rows would replace 20"):
        write(rows("x", MONTHS), roots, loss=0.1)
    assert snapshot(tmp_path) == before
    write(rows("x", MONTHS + revisions[:-1]), roots, loss=0.1)  # one lost vintage of 20 is fine


def test_one_refused_series_writes_nothing(tmp_path: Path) -> None:
    roots = silver_roots(tmp_path)
    write(pd.concat([rows("a", MONTHS), rows("b", MONTHS)]), roots)
    before = snapshot(tmp_path)
    revised_a = rows("a", [(o, k, v * 2) for o, k, v in MONTHS])
    with pytest.raises(DataLossError, match="b: "):
        write(pd.concat([revised_a, rows("b", MONTHS[:5])]), roots)
    assert snapshot(tmp_path) == before


def test_guard_reads_the_holdout_partition_too(tmp_path: Path) -> None:
    roots = silver_roots(tmp_path)
    late = [("2022-12-01", "2023-01-16 08:00", 12.0)]
    write(rows("x", MONTHS + late), roots)
    with pytest.raises(DataLossError, match="1 observation dates"):
        write(rows("x", MONTHS), roots)
