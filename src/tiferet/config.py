"""Instrument registry and cost and tax parameters from config/ (decision 0004).

`config/holdings.yaml` holds instrument identity only; `config/costs_taxes.yaml` holds every
fee and tax parameter. Every value in both files carries its source URL and check date. The
two files are joined on the instrument key.
"""

from collections.abc import Hashable
from datetime import date
from decimal import Decimal
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Any, Literal, Self

import yaml
from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator

CONFIG_DIR = Path(__file__).resolve().parents[2] / "config"
HOLDINGS_FILE = "holdings.yaml"
COSTS_TAXES_FILE = "costs_taxes.yaml"


class ConfigError(ValueError):
    """Raised when the configuration files are inconsistent with each other."""


class TaxClass(StrEnum):
    EQUITY_FUND = "equity_fund"
    BOND_FUND = "bond_fund"
    COMMODITY_FUND = "commodity_fund"
    MONEY_MARKET_FUND = "money_market_fund"
    PHYSICAL_GOLD_ETC = "physical_gold_etc"
    CRYPTO = "crypto"


FUND_TAX_CLASSES = frozenset(
    {
        TaxClass.EQUITY_FUND,
        TaxClass.BOND_FUND,
        TaxClass.COMMODITY_FUND,
        TaxClass.MONEY_MARKET_FUND,
    }
)


class LadderRole(StrEnum):
    RISKIEST = "riskiest"
    HIGH_GROWTH_EQUITY = "high_growth_equity"
    CORE_EQUITY = "core_equity"
    RISKY_EQUITY_OTHER_CYCLES = "risky_equity_other_cycles"
    LATE_CYCLE = "late_cycle"
    INFLATION = "inflation"
    INFLATION_BONDS = "inflation_bonds"
    CRASH_AND_INFLATION = "crash_and_inflation"
    CRASH_BONDS = "crash_bonds"
    CASH = "cash"


class Hedging(StrEnum):
    UNHEDGED = "unhedged"
    EUR_HEDGED = "eur_hedged"


def _check_isin(isin: str) -> str:
    """Validate the ISIN check digit (ISO 6166: Luhn over the letter-expanded code)."""
    digits = "".join(str(int(char, 36)) for char in isin[:-1])
    total = 0
    for position, char in enumerate(reversed(digits)):
        value = int(char) * (2 if position % 2 == 0 else 1)
        total += value // 10 + value % 10
    if (10 - total % 10) % 10 != int(isin[-1]):
        raise ValueError(f"invalid ISIN check digit: {isin}")
    return isin


Isin = Annotated[str, Field(pattern=r"^[A-Z]{2}[A-Z0-9]{9}[0-9]$"), AfterValidator(_check_isin)]


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Sourced[T](_Frozen):
    """A value with the source it was checked against."""

    value: T
    source_url: str = Field(pattern=r"^https://\S+$")
    checked_on: date
    verified: bool
    reason: str | None = None
    note: str | None = None

    @model_validator(mode="after")
    def _reason_matches_verification(self) -> Self:
        has_reason = self.reason is not None and self.reason.strip() != ""
        if not self.verified and not has_reason:
            raise ValueError("an unverified value needs a reason")
        if self.verified and self.reason is not None:
            raise ValueError("a reason is only allowed on an unverified value")
        return self


class Parameter[T](Sourced[T]):
    """A sourced cost or tax parameter with its unit."""

    unit: str = Field(min_length=1)


class Holding(_Frozen):
    key: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    name: Sourced[str]
    isin: Sourced[Isin | None]
    ladder_role: Sourced[LadderRole]
    tax_class: Sourced[TaxClass]
    currency_hedging: Sourced[Hedging]
    fund_launch_date: Sourced[date | None]
    exposure_available_from: Sourced[date | None]


class Holdings(_Frozen):
    holdings: tuple[Holding, ...]

    @model_validator(mode="after")
    def _unique_identity(self) -> Self:
        keys = [h.key for h in self.holdings]
        isins = [h.isin.value for h in self.holdings if h.isin.value is not None]
        if len(set(keys)) != len(keys):
            raise ValueError("duplicate holding key")
        if len(set(isins)) != len(isins):
            raise ValueError("duplicate ISIN")
        return self

    @property
    def keys(self) -> frozenset[str]:
        return frozenset(h.key for h in self.holdings)


class Broker(_Frozen):
    name: Parameter[str]
    commission_rate: Parameter[Decimal]
    commission_min: Parameter[Decimal]
    commission_max: Parameter[Decimal | None]


class FeeTier(_Frozen):
    tier: str
    min_30d_volume_usd: Decimal
    min_assets_on_platform_usd: Decimal | None
    maker: Decimal
    taker: Decimal


class NetworkFees(_Frozen):
    btc_withdrawal: Parameter[Decimal]
    eth_withdrawal: Parameter[Decimal]
    btc_transfer_to_exchange: Parameter[Decimal | None]
    eth_transfer_to_exchange: Parameter[Decimal | None]


class Crypto(_Frozen):
    exchange: Parameter[str]
    spot_fee_tiers: Parameter[tuple[FeeTier, ...]]
    network_fees: NetworkFees


class Spread(_Frozen):
    half_spread_share: Parameter[Decimal]
    estimator_corwin_schultz: Parameter[str]
    estimator_abdi_ranaldo: Parameter[str]


class MarketImpact(_Frozen):
    ignored_below_portfolio_value: Parameter[Decimal]


class Vorabpauschale(_Frozen):
    basisertrag_factor: Parameter[Decimal]
    purchase_year_reduction_denominator: Parameter[int]
    deemed_received: Parameter[str]
    credited_against_sale_gain: Parameter[str]
    basiszins_note: str
    basiszins: dict[int, Parameter[Decimal]]


class LossPot(_Frozen):
    offsets_only: Literal["capital_income", "private_sales"]
    carry_back_years: int = Field(ge=0)
    carry_forward_years: int | None = Field(ge=0)


class LossPots(_Frozen):
    capital_income: Parameter[LossPot]
    private_sales: Parameter[LossPot]


class Tax(_Frozen):
    capital_income_tax_rate: Parameter[Decimal]
    solidarity_surcharge_rate: Parameter[Decimal]
    church_tax_rate: Parameter[Decimal]
    partial_exemption: dict[TaxClass, Parameter[Decimal]]
    vorabpauschale: Vorabpauschale
    savers_allowance: Parameter[Decimal]
    loss_pots: LossPots
    sec23_exemption_threshold: Parameter[Decimal]
    sec23_holding_period: Parameter[int]
    sec23_applies_to: Parameter[tuple[TaxClass, ...]]
    personal_income_tax_rate: Parameter[Decimal]
    vat_rate: Parameter[Decimal]


class CostsTaxes(_Frozen):
    ter: dict[str, Parameter[Decimal]]
    ongoing_fees: dict[str, dict[str, Parameter[Decimal]]]
    broker: Broker
    crypto: Crypto
    spread: Spread
    market_impact: MarketImpact
    tax: Tax


class Instrument(_Frozen):
    """A holding joined with its fees."""

    holding: Holding
    ter: Parameter[Decimal]
    ongoing_fees: dict[str, Parameter[Decimal]]


class Config(_Frozen):
    holdings: Holdings
    costs_taxes: CostsTaxes
    instruments: dict[str, Instrument]


class _UniqueKeyLoader(yaml.SafeLoader):
    """Safe YAML loader that rejects duplicate mapping keys instead of keeping the last one."""

    def construct_mapping(self, node: yaml.MappingNode, deep: bool = False) -> dict[Hashable, Any]:
        seen: set[Hashable] = set()
        for key_node, _ in node.value:
            key = self.construct_object(key_node, deep=deep)
            if key in seen:
                raise ConfigError(f"duplicate key {key!r} at {key_node.start_mark}")
            seen.add(key)
        return super().construct_mapping(node, deep=deep)


def read_yaml(path: Path) -> Any:
    """Parse a YAML file with the safe loader, rejecting duplicate keys."""
    with path.open(encoding="utf-8") as stream:
        return yaml.load(stream, Loader=_UniqueKeyLoader)


def load_holdings(path: Path = CONFIG_DIR / HOLDINGS_FILE) -> Holdings:
    return Holdings.model_validate(read_yaml(path))


def load_costs_taxes(path: Path = CONFIG_DIR / COSTS_TAXES_FILE) -> CostsTaxes:
    return CostsTaxes.model_validate(read_yaml(path))


def join_instruments(holdings: Holdings, costs_taxes: CostsTaxes) -> dict[str, Instrument]:
    """Join identity and fees on the instrument key; every holding needs exactly one TER."""
    keys = holdings.keys
    ter_keys = frozenset(costs_taxes.ter)
    if keys != ter_keys:
        raise ConfigError(
            f"TER keys do not match holdings: missing {sorted(keys - ter_keys)}, "
            f"unknown {sorted(ter_keys - keys)}"
        )
    unknown_fee_keys = frozenset(costs_taxes.ongoing_fees) - keys
    if unknown_fee_keys:
        raise ConfigError(f"ongoing fees for unknown holdings: {sorted(unknown_fee_keys)}")
    return {
        h.key: Instrument(
            holding=h,
            ter=costs_taxes.ter[h.key],
            ongoing_fees=costs_taxes.ongoing_fees.get(h.key, {}),
        )
        for h in holdings.holdings
    }


def load_config(config_dir: Path = CONFIG_DIR) -> Config:
    """Load both configuration files and join them on the instrument key."""
    holdings = load_holdings(config_dir / HOLDINGS_FILE)
    costs_taxes = load_costs_taxes(config_dir / COSTS_TAXES_FILE)
    return Config(
        holdings=holdings,
        costs_taxes=costs_taxes,
        instruments=join_instruments(holdings, costs_taxes),
    )
