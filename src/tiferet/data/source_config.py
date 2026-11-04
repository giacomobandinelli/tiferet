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
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, model_validator

from tiferet.config import CONFIG_DIR, Parameter, Sourced, read_yaml
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
    attribution: str = Field(min_length=1)
    """Credit carried by every published result derived from this source (decision 0016)."""
    api_url: HttpsUrl
    api_key_env: str = Field(pattern=r"^[A-Z][A-Z0-9_]*$")
    no_vintage_errors: Sourced[tuple[str, ...]]
    """Phrases of FRED's error text, matched ignoring case, when a series has no usable
    vintage history."""
    series: tuple[FredSeries, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _phrases(self) -> Self:
        if not all(p.strip() for p in self.no_vintage_errors.value) or not (
            self.no_vintage_errors.value
        ):
            raise ValueError("no_vintage_errors needs at least one phrase")
        return self

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
    attribution: str = Field(min_length=1)
    """Credit carried by every published result derived from this source (decision 0016)."""
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


_COLUMN_HEADER = re.compile(r"<= ?\d+|[a-z0-9]+(?:[ \-][a-z0-9]+)*")
_DECILE_FIRST = re.compile(r"^(\d+)-dec$")


def column_key(name: str) -> str | None:
    """The series word of a French column header, or None for a header of unknown form.

    Case and repeated spaces are ignored. A word and its number are joined, so "Dec 2" and
    the library's "2-Dec" both give `dec2`, "Lo 10" gives `lo10` and "<= 0" gives `le0`; other
    hyphens and spaces become underscores ("Mkt-RF" gives `mkt_rf`). A header with any other
    character, such as a slash or a bracket, has no key.
    """
    text = " ".join(name.lower().split())
    if not _COLUMN_HEADER.fullmatch(text):
        return None
    text = _DECILE_FIRST.sub(r"dec\1", text).replace("<=", "le")
    text = re.sub(r"([a-z]) (\d)", r"\1\2", text)
    return re.sub(r"[^a-z0-9]+", "_", text).strip("_")


def title_key(title: str) -> str:
    """A table title for comparison: lower case, single spaces, "Weighted" as "Weight" and
    without a leading "Average", which the library uses in some files and not in others."""
    text = " ".join(title.lower().split())
    text = re.sub(r"\bweighted\b", "weight", text)
    return re.sub(r"^average ", "", text)


class FrenchFile(_Model):
    file: str = Field(pattern=r"^[A-Za-z0-9_\-]+_CSV\.zip$")
    dataset: str = Field(pattern=r"^[a-z0-9]+$")
    frequency: Frequency
    section: str | None = None
    """The title of the table, compared by `title_key`. Absent for a 3-factor file, whose
    table has no title and is found by its header."""
    columns: tuple[str, ...] = Field(min_length=1)
    market_return: bool = False
    """Also derive the market return, Mkt-RF + RF."""

    @model_validator(mode="after")
    def _shape(self) -> Self:
        if self.frequency is Frequency.QUARTERLY:
            raise ValueError("French files are daily or monthly")
        keys = [column_key(c) for c in self.columns]
        if None in keys or len(set(keys)) != len(keys):
            raise ValueError("columns must have a known header form and distinct series words")
        if self.market_return and not {"Mkt-RF", "RF"} <= set(self.columns):
            raise ValueError("market_return needs the Mkt-RF and RF columns")
        return self

    def series_id(self, column: str) -> str:
        """`kf_<dataset>_<column key>_<d|m>`, for example `kf_size_dec2_m`."""
        key = column_key(column)
        if key is None:
            raise ValueError(f"unknown column header {column!r}")
        return f"kf_{self.dataset}_{key}_{self.frequency.value[0]}"

    def series_ids(self) -> tuple[str, ...]:
        derived = ("mkt",) if self.market_return else ()
        return tuple(self.series_id(c) for c in (*self.columns, *derived))


class French(_Model):
    attribution: str = Field(min_length=1)
    """Credit carried by every published result derived from this source (decision 0016)."""
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
    attribution: str = Field(min_length=1)
    """Credit carried by every published result derived from this source (decision 0016)."""
    api_url: HttpsUrl
    datasets: tuple[EcbDataset, ...] = Field(min_length=1)


HoldingKey = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]*$")]
Currency = Annotated[str, Field(pattern=r"^[A-Z]{3}$")]


class NavPublication(_Model):
    """A NAV for date t is known `business_days_after` business days later, at `time` local
    time: TARGET days and Europe/Berlin for UCITS funds, US business days and America/New_York
    for US-listed funds."""

    business_days_after: int = Field(ge=0)
    time: time


class IsharesUcitsFund(_Model):
    """A UCITS holding whose product page is the `isin` source of its entry in
    config/holdings.yaml."""

    holding: HoldingKey
    currency: Currency
    """Currency of the NAV series, checked against "Key Facts" in the fund data file."""

    @property
    def series_id(self) -> str:
        return f"ishares_{self.holding}_nav"


class IsharesUcits(_Model):
    publication: Parameter[NavPublication]
    key_facts_sheet: str = Field(min_length=1)
    currency_label: str = Field(min_length=1)
    nav_sheet: str = Field(min_length=1)
    nav_title: str = Field(min_length=1)
    funds: tuple[IsharesUcitsFund, ...] = Field(min_length=1)


class IsharesUsFund(_Model):
    """A US-listed signal ETF."""

    ticker: str = Field(pattern=r"^[A-Z]{1,5}$")
    page_url: HttpsUrl
    currency: Currency
    """Currency of the NAV and distributions; the historical sheet does not state it."""
    distributions: bool = True
    """Whether the fund pays distributions, stored as a second series. A fund that pays none
    has no distributions series, and an ex-dividend amount in its file is refused."""

    @property
    def nav_series_id(self) -> str:
        return f"ishares_us_{self.ticker.lower()}_nav"

    @property
    def dist_series_id(self) -> str:
        return f"ishares_us_{self.ticker.lower()}_dist"

    def series_ids(self) -> tuple[str, ...]:
        """The NAV series and, for a distributing fund, the distributions series."""
        if self.distributions:
            return (self.nav_series_id, self.dist_series_id)
        return (self.nav_series_id,)


class IsharesUs(_Model):
    publication: Parameter[NavPublication]
    sheet: str = Field(min_length=1)
    header: tuple[str, str, str, str]
    """Date, NAV per share, ex-dividend amount per share, shares outstanding."""
    date_format: str = Field(min_length=1)
    """Format of a date held as text; a date held in a date cell is read as such."""
    missing: tuple[str, ...]
    """Text cells that mark a missing ex-dividend amount, besides an empty cell."""
    funds: tuple[IsharesUsFund, ...] = Field(min_length=1)


Host = Annotated[str, Field(pattern=r"^[a-z0-9]+(?:[.\-][a-z0-9]+)+$")]


class Ishares(_Model):
    attribution: str = Field(min_length=1)
    """Credit carried by every published result derived from this source (decision 0016)."""
    page_host: Host
    """Host of every product page; a page on any other host is refused."""
    data_host: Host
    """Host of the fund data link. It differs from `page_host`: the product pages link to
    files served by BlackRock, which owns iShares. A link on any other host is refused."""
    link_path: str = Field(pattern=r"^/\S+$")
    """Path fragment of the fund data link on each product page."""
    link_params: tuple[str, ...] = Field(min_length=1)
    """Query parameters the fund data link must carry."""
    ucits: IsharesUcits
    us: IsharesUs

    @model_validator(mode="after")
    def _us_pages_on_the_page_host(self) -> Self:
        for fund in self.us.funds:
            if urlsplit(fund.page_url).netloc != self.page_host:
                raise ValueError(f"the product page of {fund.ticker} is not on {self.page_host}")
        return self


class SpdrLayout(_Model):
    """Layout of the SPDR NAV history workbook, as observed by the owner.

    One sheet. Above the header, label and value rows in columns A and B. Then the header
    row, one row per day with the date as text "dd-Mon-yyyy" (English month abbreviations)
    and numbers in the other columns, newest first, and a footer row.
    """

    sheet: str = Field(min_length=1)
    labels: tuple[str, ...] = Field(min_length=1)
    """The labels of the rows above the header, each exactly once."""
    isin_label: str = Field(min_length=1)
    """Label of the row whose value must equal the fund's ISIN."""
    currency_label: str = Field(min_length=1)
    """Label of the row whose value must equal the fund's configured currency."""
    header: tuple[str, ...] = Field(min_length=2)
    """Text cells of the header row, in order."""
    date_column: str = Field(min_length=1)
    nav_column: str = Field(min_length=1)
    footer_prefix: str = Field(min_length=1)
    """Start of the text of the footer row, which ends the data."""

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if len(set(self.header)) != len(self.header) or self.date_column == self.nav_column:
            raise ValueError("header cells and the date and NAV columns must be distinct")
        if not {self.date_column, self.nav_column} <= set(self.header):
            raise ValueError("date_column and nav_column must be in the header")
        if len(set(self.labels)) != len(self.labels):
            raise ValueError("labels must be distinct")
        if self.isin_label == self.currency_label:
            raise ValueError("isin_label and currency_label must differ")
        if not {self.isin_label, self.currency_label} <= set(self.labels):
            raise ValueError("isin_label and currency_label must be in labels")
        return self


class SpdrFund(_Model):
    holding: HoldingKey
    url: HttpsUrl
    currency: Currency

    @property
    def series_id(self) -> str:
        return f"spdr_{self.holding}_nav"


class Spdr(_Model):
    attribution: str = Field(min_length=1)
    """Credit carried by every published result derived from this source (decision 0016)."""
    publication: Parameter[NavPublication]
    layout: SpdrLayout
    funds: tuple[SpdrFund, ...] = Field(min_length=1)


MetricName = Annotated[str, Field(pattern=r"^[A-Za-z0-9_]+$")]
AssetKey = Annotated[str, Field(pattern=r"^[a-z0-9]+$")]


def _positive(parameter: Parameter[Decimal], name: str) -> None:
    if parameter.value <= 0:
        raise ValueError(f"{name} must be positive")


class CoinMetricsMetrics(_Model):
    """Community metric names: price, market cap and the supply used when the cap is not free."""

    price: MetricName
    cap: MetricName
    supply: MetricName

    @model_validator(mode="after")
    def _distinct(self) -> Self:
        if len({self.price, self.cap, self.supply}) != 3:
            raise ValueError("price, cap and supply metrics must be distinct")
        return self


class CoinMetrics(_Model):
    attribution: str = Field(min_length=1)
    """Credit carried by every published result derived from this source (decision 0016)."""
    api_url: HttpsUrl
    request_interval_seconds: Parameter[Decimal]
    publication_lag_hours: Parameter[int]
    forbidden_error_type: Sourced[str]
    """`error.type` of the answer to a metric not available without a key."""
    metrics: CoinMetricsMetrics
    probe_metrics: tuple[MetricName, ...] = ()
    """Metrics `--inspect` reports on that are not ingested, such as candidate volume metrics."""
    assets: Sourced[tuple[AssetKey, ...]]

    @model_validator(mode="after")
    def _checks(self) -> Self:
        _positive(self.request_interval_seconds, "request_interval_seconds")
        _non_negative(self.publication_lag_hours, "publication_lag_hours")
        assets = self.assets.value
        if not assets or len(set(assets)) != len(assets):
            raise ValueError("assets must be a non-empty list without repeats")
        return self

    @staticmethod
    def price_series_id(asset: str) -> str:
        return f"cm_{asset}_price_usd"

    @staticmethod
    def cap_series_id(asset: str) -> str:
        return f"cm_{asset}_cap_usd"

    def series_ids(self) -> tuple[str, ...]:
        return tuple(
            i for a in self.assets.value for i in (self.price_series_id(a), self.cap_series_id(a))
        )


class Stablecoin(_Model):
    symbol: str = Field(pattern=r"^[A-Z]+$")
    llama_id: str = Field(pattern=r"^[0-9]+$")
    """DefiLlama's stablecoin id, the `{asset}` of `/stablecoin/{asset}`."""

    @property
    def series_id(self) -> str:
        return f"defillama_{self.symbol.lower()}_cap_usd"


class DefiLlamaLayout(_Model):
    """Keys of a `/stablecoin/{asset}` answer: the history list, each item's date (unix
    seconds) and the path to its circulating amount in pegged US dollars."""

    symbol_key: str = Field(min_length=1)
    history_key: str = Field(min_length=1)
    date_key: str = Field(min_length=1)
    value_path: tuple[str, ...] = Field(min_length=1)


class DefiLlama(_Model):
    attribution: str = Field(min_length=1)
    """Credit carried by every published result derived from this source (decision 0016)."""
    api_url: HttpsUrl
    request_interval_seconds: Parameter[Decimal]
    publication_lag_hours: Parameter[int]
    layout: Sourced[DefiLlamaLayout]
    stablecoins: Sourced[tuple[Stablecoin, ...]]

    @model_validator(mode="after")
    def _checks(self) -> Self:
        _positive(self.request_interval_seconds, "request_interval_seconds")
        _non_negative(self.publication_lag_hours, "publication_lag_hours")
        coins = self.stablecoins.value
        if not coins:
            raise ValueError("stablecoins must not be empty")
        for field in ("symbol", "llama_id"):
            values = [getattr(c, field) for c in coins]
            if len(set(values)) != len(values):
                raise ValueError(f"stablecoin {field} values repeat")
        return self


class OcpFrequency(StrEnum):
    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"


class ReturnUnits(StrEnum):
    DECIMAL = "decimal"
    PERCENT = "percent"


class OcpFile(_Model):
    """One Open Crypto Pricing factor file: its path under `ocp.base_url`, its date columns
    (one, or week start and end for the weekly file) and its value columns in order."""

    path: str = Field(pattern=r"^[a-z0-9_]+(/[a-z0-9_]+)*\.csv$")
    frequency: OcpFrequency
    date_columns: tuple[str, ...] = Field(min_length=1, max_length=2)
    columns: tuple[str, ...] = Field(min_length=1)
    count_columns: tuple[str, ...] = ()
    """Columns holding counts (non-negative integers), stored as they are."""
    return_units: ReturnUnits

    @model_validator(mode="after")
    def _shape(self) -> Self:
        names = [*self.date_columns, *self.columns]
        if len(set(names)) != len(names) or not all(re.fullmatch(r"[A-Za-z_]+", n) for n in names):
            raise ValueError("OCP columns must be distinct names of letters and underscores")
        if (self.frequency is OcpFrequency.WEEKLY) != (len(self.date_columns) == 2):
            raise ValueError("the weekly file has a start and an end column; others have one")
        if not set(self.count_columns) <= set(self.columns):
            raise ValueError("count_columns must be among columns")
        return self

    def series_id(self, column: str) -> str:
        """`ocp_cg_<column>_<d|w|m>`, for example `ocp_cg_cmkt_w`."""
        return f"ocp_cg_{column.lower()}_{self.frequency.value[0]}"

    def series_ids(self) -> tuple[str, ...]:
        return tuple(self.series_id(c) for c in self.columns)


class Ocp(_Model):
    attribution: str = Field(min_length=1)
    """Credit carried by every published result derived from this source (decision 0016)."""
    base_url: HttpsUrl
    publication_lag_days: Parameter[int]
    files: Sourced[tuple[OcpFile, ...]]

    @model_validator(mode="after")
    def _checks(self) -> Self:
        _non_negative(self.publication_lag_days, "publication_lag_days")
        if not self.files.value:
            raise ValueError("files must not be empty")
        return self


class Sources(_Model):
    silver: SilverSettings
    fred: Fred
    shiller: Shiller
    french: French
    ecb: Ecb
    ishares: Ishares
    spdr: Spdr
    coinmetrics: CoinMetrics
    defillama: DefiLlama
    ocp: Ocp

    def series_ids(self) -> list[str]:
        """Every silver series id the configuration produces."""
        return [
            *(s.series_id for s in self.fred.series),
            *(s.series_id for s in self.shiller.series),
            *(i for f in self.french.files for i in f.series_ids()),
            *(s.series_id for d in self.ecb.datasets for s in d.series),
            *(f.series_id for f in self.ishares.ucits.funds),
            *(i for f in self.ishares.us.funds for i in f.series_ids()),
            *(f.series_id for f in self.spdr.funds),
            *self.coinmetrics.series_ids(),
            *(c.series_id for c in self.defillama.stablecoins.value),
            *(i for f in self.ocp.files.value for i in f.series_ids()),
        ]

    def attributions(self) -> dict[str, str]:
        """The credit of every source, by its section name in config/sources.yaml."""
        return {
            name: getattr(self, name).attribution
            for name in type(self).model_fields
            if name != "silver"
        }

    def host_intervals(self) -> dict[str, float]:
        """Minimum seconds between requests to a host, where a source configures one."""
        return {
            urlsplit(self.coinmetrics.api_url).netloc: float(
                self.coinmetrics.request_interval_seconds.value
            ),
            urlsplit(self.defillama.api_url).netloc: float(
                self.defillama.request_interval_seconds.value
            ),
        }

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
