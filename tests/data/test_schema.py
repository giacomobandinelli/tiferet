"""Tests for the silver point-in-time schema (decision 0010)."""

import pandas as pd
import pytest

from tiferet.data.schema import (
    KNOWN_AT_DTYPE,
    OBS_DATE_DTYPE,
    PIT_SCHEMA,
    SilverSchemaError,
    empty_silver,
    validate_silver,
)


def frame(**overrides: object) -> pd.DataFrame:
    columns: dict[str, object] = {
        "series_id": ["cpi"],
        "obs_date": pd.to_datetime(["2022-01-01"]),
        "known_at": pd.to_datetime(["2022-02-15 08:00"]).tz_localize("Europe/Berlin"),
        "value": [100.0],
    }
    columns.update(overrides)
    return pd.DataFrame(columns)


def test_valid_frame_gets_canonical_dtypes() -> None:
    validated = validate_silver(frame())
    assert validated["obs_date"].dtype == OBS_DATE_DTYPE
    assert validated["known_at"].dtype == KNOWN_AT_DTYPE
    assert validated["known_at"].iloc[0] == pd.Timestamp("2022-02-15 07:00", tz="UTC")


def test_empty_frame_is_valid() -> None:
    assert validate_silver(empty_silver()).empty


@pytest.mark.parametrize(
    "bad",
    [
        frame(known_at=pd.to_datetime(["2022-02-15"])),
        frame(obs_date=pd.to_datetime(["2022-01-01"]).tz_localize("UTC")),
        frame(obs_date=pd.to_datetime(["2022-01-01 12:00"])),
        frame(known_at=pd.to_datetime(["2021-12-31"]).tz_localize("UTC")),
        frame(value=[None]),
        frame(known_at=pd.Series([pd.NaT], dtype="datetime64[us, UTC]")),
        frame(series_id=["../cpi"]),
        frame().drop(columns="known_at"),
        frame(extra=[1]),
        pd.concat([frame(), frame()]),
    ],
    ids=[
        "naive-known-at",
        "aware-obs-date",
        "obs-date-not-midnight",
        "known-before-period",
        "null-value",
        "null-known-at",
        "path-in-series-id",
        "missing-column",
        "extra-column",
        "duplicate-version",
    ],
)
def test_invalid_frames_are_refused(bad: pd.DataFrame) -> None:
    with pytest.raises(SilverSchemaError):
        validate_silver(bad)


def test_revision_is_a_valid_second_version() -> None:
    later = frame(
        known_at=pd.to_datetime(["2022-03-15"]).tz_localize("Europe/Berlin"), value=[101.0]
    )
    assert len(validate_silver(pd.concat([frame(), later]))) == 2


def test_point_in_time_view_holds_one_version_per_observation() -> None:
    later = frame(
        known_at=pd.to_datetime(["2022-03-15"]).tz_localize("Europe/Berlin"), value=[101.0]
    )
    with pytest.raises(SilverSchemaError):
        validate_silver(pd.concat([frame(), later]), PIT_SCHEMA)
