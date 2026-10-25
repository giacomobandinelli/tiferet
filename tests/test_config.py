"""Sourcing and consistency checks for config/holdings.yaml and config/costs_taxes.yaml."""

import re
from collections.abc import Iterator
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from tiferet.config import (
    CONFIG_DIR,
    COSTS_TAXES_FILE,
    FUND_TAX_CLASSES,
    HOLDINGS_FILE,
    ConfigError,
    Holding,
    LadderRole,
    Sourced,
    TaxClass,
    join_instruments,
    load_config,
    load_costs_taxes,
    load_holdings,
    read_yaml,
)

PLAN = Path(__file__).resolve().parents[1] / "docs" / "plan.md"
CONFIG_FILES = [HOLDINGS_FILE, COSTS_TAXES_FILE]
# Keys that may hold a bare value outside a sourced entry: the instrument key and free text.
UNSOURCED_KEYS = {"key", "note"}
FEE_KEY = re.compile(r"(^|_)(ter|fees?|costs?|commissions?|expenses?|charges?)($|_)")


def _walk(node: Any, path: str = "") -> Iterator[tuple[str, Any]]:
    yield path, node
    if isinstance(node, dict):
        for key, child in node.items():
            yield from _walk(child, f"{path}.{key}")
    elif isinstance(node, list):
        for index, child in enumerate(node):
            yield from _walk(child, f"{path}[{index}]")


def _is_entry(node: Any) -> bool:
    return isinstance(node, dict) and "value" in node


def _bare_values(node: Any, path: str = "") -> Iterator[str]:
    """Paths of scalar values that are not inside a sourced entry."""
    if _is_entry(node):
        return
    if isinstance(node, dict):
        for key, child in node.items():
            if isinstance(child, dict | list):
                yield from _bare_values(child, f"{path}.{key}")
            elif str(key) not in UNSOURCED_KEYS and not str(key).endswith("_note"):
                yield f"{path}.{key}"
    elif isinstance(node, list):
        for index, child in enumerate(node):
            yield from _bare_values(child, f"{path}[{index}]")


def _plan_holdings() -> list[tuple[str, str, str | None]]:
    """(ladder role, holding name, ISIN or None) for each row of the table in plan section 3.1."""
    section = PLAN.read_text(encoding="utf-8").split("### 3.1 Holdings", 1)[1]
    section = section.split("###", 1)[0]
    rows = []
    for line in section.splitlines():
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) == 4 and cells[0] not in {"Ladder role", "---"}:
            isin = cells[2] if re.fullmatch(r"[A-Z]{2}[A-Z0-9]{10}", cells[2]) else None
            rows.append((cells[0], cells[1], isin))
    return rows


def _role_from_plan(text: str) -> LadderRole:
    return LadderRole(re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_"))


@pytest.fixture(scope="module")
def raw() -> dict[str, Any]:
    return {name: read_yaml(CONFIG_DIR / name) for name in CONFIG_FILES}


def test_config_loads_and_joins() -> None:
    config = load_config()
    assert config.instruments.keys() == config.holdings.keys


@pytest.mark.parametrize("name", CONFIG_FILES)
def test_every_entry_has_source_and_check_date(raw: dict[str, Any], name: str) -> None:
    entries = [(path, node) for path, node in _walk(raw[name]) if _is_entry(node)]
    assert entries
    for path, node in entries:
        assert isinstance(node.get("source_url"), str), path
        assert node["source_url"].startswith("https://"), path
        assert isinstance(node.get("checked_on"), date), path
        assert node["checked_on"] <= date.today(), path
        assert isinstance(node.get("verified"), bool), path
        if not node["verified"]:
            assert str(node.get("reason") or "").strip(), path


@pytest.mark.parametrize("name", CONFIG_FILES)
def test_no_value_outside_a_sourced_entry(raw: dict[str, Any], name: str) -> None:
    assert list(_bare_values(raw[name])) == []


def test_costs_carry_units(raw: dict[str, Any]) -> None:
    for path, node in _walk(raw[COSTS_TAXES_FILE]):
        if _is_entry(node):
            assert str(node.get("unit") or "").strip(), path


def test_every_holding_has_exactly_one_ter_and_vice_versa(raw: dict[str, Any]) -> None:
    holding_keys = [h["key"] for h in raw[HOLDINGS_FILE]["holdings"]]
    ter_keys = list(raw[COSTS_TAXES_FILE]["ter"])
    assert len(holding_keys) == len(set(holding_keys))
    assert sorted(holding_keys) == sorted(ter_keys)


def test_ter_lives_only_in_costs_taxes(raw: dict[str, Any]) -> None:
    keys = [path.rsplit(".", 1)[-1] for path, _ in _walk(raw[HOLDINGS_FILE])]
    assert [key for key in keys if FEE_KEY.search(key)] == []
    holding = raw[HOLDINGS_FILE]["holdings"][0]
    with pytest.raises(ValidationError):
        Holding.model_validate({**holding, "ter": holding["name"]})


def test_holdings_match_plan_section_3_1() -> None:
    plan = _plan_holdings()
    holdings = load_holdings().holdings
    assert len(plan) == 13
    assert len(holdings) == 13
    for (role, name, isin), holding in zip(plan, holdings, strict=True):
        assert holding.isin.value == isin, name
        assert holding.ladder_role.value == _role_from_plan(role), name


def test_tax_classes_are_valid_and_covered(raw: dict[str, Any]) -> None:
    valid = {tax_class.value for tax_class in TaxClass}
    for holding in raw[HOLDINGS_FILE]["holdings"]:
        assert holding["tax_class"]["value"] in valid, holding["key"]
    tax = load_costs_taxes().tax
    used = {h.tax_class.value for h in load_holdings().holdings}
    assert used & FUND_TAX_CLASSES <= tax.partial_exemption.keys()
    assert set(tax.partial_exemption) <= FUND_TAX_CLASSES
    assert used - FUND_TAX_CLASSES == set(tax.sec23_applies_to.value)


def test_basiszins_covers_every_published_year() -> None:
    basiszins = load_costs_taxes().tax.vorabpauschale.basiszins
    assert sorted(basiszins) == list(range(2018, 2027))


def test_xetra_gold_custody_includes_vat() -> None:
    config = load_config()
    custody = config.instruments["xetra_gold"].ongoing_fees["custody"].value
    vat = config.costs_taxes.tax.vat_rate.value
    assert custody * (1 + vat) == Decimal("0.00357")


def test_unverified_value_requires_reason() -> None:
    base = {"value": 1, "source_url": "https://example.org", "checked_on": date(2026, 10, 25)}
    assert Sourced[int].model_validate({**base, "verified": True}).value == 1
    with pytest.raises(ValidationError):
        Sourced[int].model_validate({**base, "verified": False})
    with pytest.raises(ValidationError):
        Sourced[int].model_validate({**base, "verified": False, "reason": " "})
    with pytest.raises(ValidationError):
        Sourced[int].model_validate({**base, "verified": True, "reason": "unneeded"})


@pytest.mark.parametrize(
    "change",
    [
        {"source_url": ""},
        {"source_url": "http://example.org"},
        {"checked_on": None},
    ],
)
def test_entry_without_valid_source_is_rejected(change: dict[str, Any]) -> None:
    entry = {
        "value": 1,
        "source_url": "https://example.org",
        "checked_on": date(2026, 10, 25),
        "verified": True,
    }
    with pytest.raises(ValidationError):
        Sourced[int].model_validate({**entry, **change})


def test_invalid_isin_check_digit_is_rejected(raw: dict[str, Any]) -> None:
    holding = raw[HOLDINGS_FILE]["holdings"][3]
    isin = {**holding["isin"], "value": "IE00B5BMR088"}
    with pytest.raises(ValidationError):
        Holding.model_validate({**holding, "isin": isin})


def test_join_rejects_missing_ter() -> None:
    holdings = load_holdings()
    costs_taxes = load_costs_taxes()
    ter = dict(costs_taxes.ter)
    ter.pop("sp500")
    with pytest.raises(ConfigError, match="sp500"):
        join_instruments(holdings, costs_taxes.model_copy(update={"ter": ter}))


def test_duplicate_yaml_keys_are_rejected(tmp_path: Path) -> None:
    path = tmp_path / "duplicate.yaml"
    path.write_text("ter:\n  sp500: 1\n  sp500: 2\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="duplicate key"):
        read_yaml(path)
