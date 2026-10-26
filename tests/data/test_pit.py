"""Tests for the point-in-time accessor and its modes (decisions 0002, 0008, 0010)."""

from datetime import date, datetime, timedelta

import pandas as pd
import pytest

from tiferet.data import pit
from tiferet.data.pit import (
    DEVELOPMENT_END,
    UNSEAL_FLAG,
    HoldoutSealedError,
    Mode,
    NaiveTimestampError,
    holdout_unsealed,
)
from tiferet.data.store import BERLIN, HOLDOUT_START, InMemoryStore

SERIES = "cpi"
UNSEALED = {UNSEAL_FLAG: "1"}


def rows(*entries: tuple[str, str, float]) -> pd.DataFrame:
    obs, known, value = zip(*entries, strict=True)
    return pd.DataFrame(
        {
            "series_id": SERIES,
            "obs_date": pd.to_datetime(list(obs)),
            "known_at": pd.to_datetime(list(known), utc=True),
            "value": list(value),
        }
    )


STORE = InMemoryStore(
    rows(
        ("2022-10-01", "2022-11-15 07:00", 100.0),
        ("2022-11-01", "2022-12-15 07:00", 101.0),
        ("2022-10-01", "2022-12-15 07:00", 100.5),
        ("2022-12-01", "2023-01-16 07:00", 102.0),
    )
)


class SpyStore:
    """Records the partitions each read asks for."""

    def __init__(self) -> None:
        self.calls: list[bool] = []

    def read(self, series_id: str, *, include_holdout: bool) -> pd.DataFrame:
        self.calls.append(include_holdout)
        return STORE.read(series_id, include_holdout=include_holdout)


def at(text: str) -> pd.Timestamp:
    return pd.Timestamp(text, tz="UTC")


def values(frame: pd.DataFrame) -> list[float]:
    return [float(v) for v in frame["value"]]


def test_development_end_precedes_holdout_start() -> None:
    assert DEVELOPMENT_END < HOLDOUT_START
    assert pd.Timedelta(seconds=1) == HOLDOUT_START - DEVELOPMENT_END


@pytest.mark.parametrize(
    ("env", "expected"),
    [({}, False), ({UNSEAL_FLAG: "0"}, False), ({UNSEAL_FLAG: "true"}, False), (UNSEALED, True)],
)
def test_holdout_unsealed_only_for_exactly_one(env: dict[str, str], expected: bool) -> None:
    assert holdout_unsealed(env) is expected


def test_research_refuses_a_moment_after_development_end() -> None:
    just_after = DEVELOPMENT_END + pd.Timedelta(microseconds=1)
    with pytest.raises(HoldoutSealedError):
        pit.get(SERIES, just_after, mode=Mode.RESEARCH, store=STORE, env={})


def test_research_accepts_development_end() -> None:
    frame = pit.get(SERIES, DEVELOPMENT_END, mode=Mode.RESEARCH, store=STORE, env={})
    assert values(frame) == [100.5, 101.0]


def test_research_refusal_compares_instants_not_wall_clocks() -> None:
    # 23:30 UTC on 31 December is already 1 January in Berlin.
    with pytest.raises(HoldoutSealedError):
        pit.get(SERIES, at("2022-12-31 23:30"), mode=Mode.RESEARCH, store=STORE, env={})


def test_sealed_research_never_reads_the_holdout_partition() -> None:
    spy = SpyStore()
    pit.get(SERIES, DEVELOPMENT_END, mode=Mode.RESEARCH, store=spy, env={})
    assert spy.calls == [False]


def test_unsealed_research_reads_the_holdout() -> None:
    spy = SpyStore()
    frame = pit.get(SERIES, at("2023-02-01"), mode=Mode.RESEARCH, store=spy, env=UNSEALED)
    assert spy.calls == [True]
    assert values(frame) == [100.5, 101.0, 102.0]


def test_production_reads_current_data() -> None:
    frame = pit.get(SERIES, at("2023-02-01"), mode=Mode.PRODUCTION, store=STORE, env={})
    assert values(frame) == [100.5, 101.0, 102.0]


@pytest.mark.parametrize("as_of", [datetime(2022, 6, 1), pd.Timestamp("2022-06-01")])
def test_naive_as_of_is_refused(as_of: datetime) -> None:
    with pytest.raises(NaiveTimestampError):
        pit.get(SERIES, as_of, mode=Mode.PRODUCTION, store=STORE, env={})


def test_mode_must_be_a_mode() -> None:
    with pytest.raises(TypeError):
        pit.get(SERIES, at("2022-06-01"), mode="production", store=STORE, env={})  # type: ignore[arg-type]


def test_revision_is_invisible_before_its_known_at() -> None:
    revised_at = at("2022-12-15 07:00")
    before = pit.get(
        SERIES, revised_at - timedelta(microseconds=1), mode=Mode.RESEARCH, store=STORE, env={}
    )
    after = pit.get(SERIES, revised_at, mode=Mode.RESEARCH, store=STORE, env={})
    assert values(before) == [100.0]
    assert values(after) == [100.5, 101.0]
    assert (after["known_at"] <= revised_at).all()


def test_as_of_in_another_time_zone() -> None:
    berlin = datetime(2022, 12, 15, 8, 0, tzinfo=BERLIN)  # 07:00 UTC
    frame = pit.get(SERIES, berlin, mode=Mode.RESEARCH, store=STORE, env={})
    assert values(frame) == [100.5, 101.0]


def test_start_filters_observations() -> None:
    frame = pit.get(
        SERIES, DEVELOPMENT_END, date(2022, 11, 1), mode=Mode.RESEARCH, store=STORE, env={}
    )
    assert list(frame["obs_date"]) == [pd.Timestamp("2022-11-01")]


def test_nothing_known_yet_gives_an_empty_frame() -> None:
    frame = pit.get(SERIES, at("2022-01-01"), mode=Mode.RESEARCH, store=STORE, env={})
    assert frame.empty
    assert list(frame.columns) == ["series_id", "obs_date", "known_at", "value"]
    assert isinstance(frame["known_at"].dtype, pd.DatetimeTZDtype)


def test_result_is_sorted_and_unique_per_observation() -> None:
    frame = pit.get(SERIES, at("2023-02-01"), mode=Mode.PRODUCTION, store=STORE, env={})
    assert frame["obs_date"].is_monotonic_increasing
    assert frame["obs_date"].is_unique
