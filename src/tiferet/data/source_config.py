"""Source configuration from config/sources.yaml (decisions 0013, 0014).

Series lists, file names and keys are plain identity configuration. Every lag, publication
time and tolerance is a sourced `Parameter`, checked against its source like the cost and tax
parameters in config/costs_taxes.yaml.
"""

import re
from datetime import time
from decimal import Decimal
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from tiferet.config import CONFIG_DIR, Parameter, read_yaml
from tiferet.data.schema import SERIES_ID_PATTERN
from tiferet.data.timing import Frequency

SOURCES_FILE = "sources.yaml"

SeriesId = Annotated[str, Field(pattern=SERIES_ID_PATTERN)]
HttpsUrl = Annotated[str, Field(pattern=r"^https://\S+$")]


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


def _non_negative(parameter: Parameter[int], name: str) -> None:
    if parameter.value < 0:
        raise ValueError(f"{name} cannot be negative")


class SilverSettings(_Model):
    max_row_loss_fraction: Parameter[Decimal]

    @model_validator(mode="after")
    def _fraction(self) -> Self:
        if not 0 <= self.max_row_loss_fraction.value < 1:
            raise ValueError("max_row_loss_fraction must be in [0, 1)")
        return self


class FredSeries(_Model):
    series_id: SeriesId
    fred_id: str = Field(pattern=r"^[A-Z0-9_]+$")
    frequency: Frequency
    units: str = Field(min_length=1)
    publication_lag_days: Parameter[int]

    @model_validator(mode="after")
    def _lag(self) -> Self:
        _non_negative(self.publication_lag_days, "publication_lag_days")
        return self


class Fred(_Model):
    api_url: HttpsUrl
    api_key_env: str = Field(pattern=r"^[A-Z][A-Z0-9_]*$")
    series: tuple[FredSeries, ...] = Field(min_length=1)

    def by_id(self, series_id: str) -> FredSeries:
        return next(s for s in self.series if s.series_id == series_id)


class ShillerRule(StrEnum):
    MONTH_END = "month_end"
    """End of the month plus `price_lag_days`."""
    QUARTER_END = "quarter_end"
    """End of the calendar quarter holding the month plus `earnings_dividends_lag_days`."""
    FOLLOWS = "follows"
    """End of the month plus the lag of the FRED series named in `follows`."""


class ShillerSeries(_Model):
    series_id: SeriesId
    header: str = Field(min_length=1)
    units: str = Field(min_length=1)
    rule: ShillerRule
    follows: SeriesId | None = None
    annual_before: Parameter[int] | None = None
    """First year of monthly or quarterly source data; earlier months are interpolated
    from annual figures."""

    @model_validator(mode="after")
    def _follows_matches_rule(self) -> Self:
        if (self.rule is ShillerRule.FOLLOWS) != (self.follows is not None):
            raise ValueError("follows is required for, and only for, the follows rule")
        return self


class Shiller(_Model):
    page_url: HttpsUrl
    link_name: str = Field(pattern=r"^[A-Za-z0-9_.\-]+$")
    sheet: str = Field(min_length=1)
    date_header: str = Field(min_length=1)
    price_lag_days: Parameter[int]
    earnings_dividends_lag_days: Parameter[int]
    series: tuple[ShillerSeries, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _lags(self) -> Self:
        _non_negative(self.price_lag_days, "price_lag_days")
        _non_negative(self.earnings_dividends_lag_days, "earnings_dividends_lag_days")
        return self


class FrenchFile(_Model):
    file: str = Field(pattern=r"^[A-Za-z0-9_\-]+_CSV\.zip$")
    dataset: str = Field(pattern=r"^[a-z0-9]+$")
    frequency: Frequency
    section: str | None = None
    """The title of the table, ignoring case and repeated spaces; the first table if absent."""
    columns: tuple[str, ...] = Field(min_length=1)
    market_return: bool = False
    """Also derive the market return, Mkt-RF + RF."""

    @model_validator(mode="after")
    def _shape(self) -> Self:
        if self.frequency is Frequency.QUARTERLY:
            raise ValueError("French files are daily or monthly")
        if self.market_return and not {"Mkt-RF", "RF"} <= set(self.columns):
            raise ValueError("market_return needs the Mkt-RF and RF columns")
        return self

    def series_id(self, column: str) -> str:
        """`kf_<dataset>_<column>_<d|m>`, the column reduced to lower-case words."""
        name = re.sub(r"[^a-z0-9]+", "_", column.lower()).strip("_")
        return f"kf_{self.dataset}_{name}_{self.frequency.value[0]}"

    def series_ids(self) -> tuple[str, ...]:
        derived = ("mkt",) if self.market_return else ()
        return tuple(self.series_id(c) for c in (*self.columns, *derived))


class French(_Model):
    base_url: HttpsUrl
    publication_lag_days: Parameter[int]
    files: tuple[FrenchFile, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _lag(self) -> Self:
        _non_negative(self.publication_lag_days, "publication_lag_days")
        return self


class Publication(_Model):
    """`known_at` is `target_days_after` TARGET business days after the observation, at
    `time` in Europe/Berlin."""

    target_days_after: int = Field(ge=0)
    time: time


class EcbSeries(_Model):
    series_id: SeriesId
    key: str = Field(pattern=r"^[A-Za-z0-9_]+(\.[A-Za-z0-9_]+)+$")
    units: str = Field(min_length=1)


class EcbDataset(_Model):
    flow: str = Field(pattern=r"^[A-Z0-9_]+$")
    publication: Parameter[Publication]
    series: tuple[EcbSeries, ...] = Field(min_length=1)


class Ecb(_Model):
    api_url: HttpsUrl
    datasets: tuple[EcbDataset, ...] = Field(min_length=1)


class Sources(_Model):
    silver: SilverSettings
    fred: Fred
    shiller: Shiller
    french: French
    ecb: Ecb

    def series_ids(self) -> list[str]:
        """Every silver series id the configuration produces."""
        return [
            *(s.series_id for s in self.fred.series),
            *(s.series_id for s in self.shiller.series),
            *(i for f in self.french.files for i in f.series_ids()),
            *(s.series_id for d in self.ecb.datasets for s in d.series),
        ]

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        ids = self.series_ids()
        duplicates = sorted({i for i in ids if ids.count(i) > 1})
        if duplicates:
            raise ValueError(f"duplicate series ids: {duplicates}")
        fred_ids = [s.fred_id for s in self.fred.series]
        if len(set(fred_ids)) != len(fred_ids):
            raise ValueError("duplicate FRED ids")
        known = {s.series_id for s in self.fred.series}
        missing = {s.follows for s in self.shiller.series if s.follows} - known
        if missing:
            raise ValueError(f"Shiller series follow unknown FRED series: {sorted(missing)}")
        return self


def load_sources(path: Path = CONFIG_DIR / SOURCES_FILE) -> Sources:
    return Sources.model_validate(read_yaml(path))
