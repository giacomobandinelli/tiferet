"""Schema of silver point-in-time tables (plan §5.2; decisions 0003, 0010).

A silver table is long: one row per series, observation period and version. `obs_date` is
the period a value refers to, as a naive date. `known_at` is the moment that version became
known, as a UTC timestamp. A revision is a further row with a later `known_at`.
"""

import pandas as pd
import pandera.errors
import pandera.pandas as pa

SERIES_ID = "series_id"
OBS_DATE = "obs_date"
KNOWN_AT = "known_at"
VALUE = "value"
COLUMNS = (SERIES_ID, OBS_DATE, KNOWN_AT, VALUE)

OBS_DATE_DTYPE = "datetime64[us]"
KNOWN_AT_DTYPE = pd.DatetimeTZDtype("us", "UTC")
SERIES_ID_PATTERN = r"^[a-z0-9_]+$"


class SilverSchemaError(ValueError):
    """Raised when a frame does not satisfy the silver point-in-time schema."""


def _known_after_period_start(frame: pd.DataFrame) -> pd.Series:
    known_after: pd.Series = frame[KNOWN_AT] >= frame[OBS_DATE].dt.tz_localize("UTC")
    return known_after


def _schema(unique: list[str]) -> pa.DataFrameSchema:
    return pa.DataFrameSchema(
        {
            SERIES_ID: pa.Column(str, pa.Check.str_matches(SERIES_ID_PATTERN)),
            OBS_DATE: pa.Column(OBS_DATE_DTYPE, pa.Check(lambda s: (s == s.dt.normalize()).all())),
            KNOWN_AT: pa.Column(KNOWN_AT_DTYPE),
            VALUE: pa.Column(float),
        },
        checks=[pa.Check(_known_after_period_start, error="known_at before obs_date")],
        unique=unique,
        strict=True,
    )


SILVER_SCHEMA = _schema([SERIES_ID, OBS_DATE, KNOWN_AT])
"""Every stored version: one row per series, observation period and `known_at`."""

PIT_SCHEMA = _schema([SERIES_ID, OBS_DATE])
"""A point-in-time view: the one version of each observation known at `as_of`."""


def _normalise_timestamps(frame: pd.DataFrame) -> pd.DataFrame:
    """Bring timestamps to one resolution; refuse a naive `known_at` or an aware `obs_date`.

    A naive `known_at` is refused rather than assumed to be UTC, because guessing a time zone
    can move a value across the moment it became known.
    """
    for column in (OBS_DATE, KNOWN_AT):
        if column not in frame.columns:
            raise SilverSchemaError(f"missing column {column!r}")
    known_at, obs_date = frame[KNOWN_AT], frame[OBS_DATE]
    if not isinstance(known_at.dtype, pd.DatetimeTZDtype):
        raise SilverSchemaError(f"known_at must be timezone-aware, got {known_at.dtype}")
    if not pd.api.types.is_datetime64_dtype(obs_date) or isinstance(
        obs_date.dtype, pd.DatetimeTZDtype
    ):
        raise SilverSchemaError(f"obs_date must be a naive datetime, got {obs_date.dtype}")
    return frame.reset_index(drop=True).assign(
        **{
            OBS_DATE: obs_date.dt.as_unit("us").to_numpy(),
            KNOWN_AT: known_at.dt.tz_convert("UTC").dt.as_unit("us").array,
        }
    )


def validate_silver(
    frame: pd.DataFrame, schema: pa.DataFrameSchema = SILVER_SCHEMA
) -> pd.DataFrame:
    """Return `frame` with canonical dtypes, or raise `SilverSchemaError`."""
    normalised = _normalise_timestamps(frame)
    try:
        validated: pd.DataFrame = schema.validate(normalised)
    except (pandera.errors.SchemaError, pandera.errors.SchemaErrors) as error:
        raise SilverSchemaError(str(error)) from error
    return validated


def empty_silver() -> pd.DataFrame:
    """An empty frame with the silver columns and dtypes."""
    return pd.DataFrame(
        {
            SERIES_ID: pd.Series(dtype=str),
            OBS_DATE: pd.Series(dtype=OBS_DATE_DTYPE),
            KNOWN_AT: pd.Series(dtype=KNOWN_AT_DTYPE),
            VALUE: pd.Series(dtype=float),
        }
    )
