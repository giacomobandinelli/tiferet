"""Sourcing and consistency checks for config/sources.yaml (decisions 0013, 0014)."""

import re
from collections.abc import Iterator
from typing import Any

import pytest
from pydantic import ValidationError

from tiferet.config import CONFIG_DIR, read_yaml
from tiferet.data.schema import SERIES_ID_PATTERN
from tiferet.data.source_config import (
    SOURCES_FILE,
    Sources,
    column_key,
    load_sources,
    title_key,
)

RAW = read_yaml(CONFIG_DIR / SOURCES_FILE)


def _bare_numbers(node: Any, path: str = "") -> Iterator[str]:
    """Paths of numbers that are not inside a sourced entry."""
    if isinstance(node, dict):
        if "value" in node:
            return
        for key, child in node.items():
            yield from _bare_numbers(child, f"{path}.{key}")
    elif isinstance(node, list):
        for index, child in enumerate(node):
            yield from _bare_numbers(child, f"{path}[{index}]")
    elif isinstance(node, int | float) and not isinstance(node, bool):
        yield path


def _entries(node: Any) -> Iterator[dict[str, Any]]:
    if isinstance(node, dict):
        if "value" in node:
            yield node
            return
        for child in node.values():
            yield from _entries(child)
    elif isinstance(node, list):
        for child in node:
            yield from _entries(child)


def test_configuration_loads() -> None:
    sources = load_sources()
    assert len(sources.series_ids()) == len(set(sources.series_ids()))
    assert all(re.fullmatch(SERIES_ID_PATTERN, s) for s in sources.series_ids())


def test_every_number_is_a_sourced_value() -> None:
    assert list(_bare_numbers(RAW)) == []


def test_every_entry_names_a_https_source_and_check_date() -> None:
    entries = list(_entries(RAW))
    assert entries
    for entry in entries:
        assert entry["source_url"].startswith("https://")
        assert entry["checked_on"] is not None
        assert entry["verified"] or entry.get("reason")


def test_initial_fred_list() -> None:
    fred_ids = {s.fred_id for s in load_sources().fred.series}
    assert fred_ids == {
        "CPIAUCSL",
        "UNRATE",
        "DGS3MO",
        "DGS2",
        "DGS10",
        "DFF",
        "NASDAQCOM",
        "NASDAQ100",
        "DCOILWTICO",
        "BAA10Y",
    }


def test_french_files_cover_daily_and_monthly_of_each_dataset() -> None:
    files = load_sources().french.files
    datasets = {f.dataset for f in files}
    assert datasets == {"us3", "ind49", "size", "dev3", "eur3", "jpn3", "apxj3", "nam3"}
    for dataset in datasets:
        assert {f.frequency.value for f in files if f.dataset == dataset} == {"daily", "monthly"}
    ind49 = next(f for f in files if f.dataset == "ind49")
    assert len(ind49.columns) == 49


def edited(path: tuple[str | int, ...], value: Any) -> dict[str, Any]:
    raw: dict[str, Any] = read_yaml(CONFIG_DIR / SOURCES_FILE)
    node: Any = raw
    for step in path[:-1]:
        node = node[step]
    node[path[-1]] = value
    return raw


@pytest.mark.parametrize(
    ("path", "value", "message"),
    [
        (("fred", "series", 1, "series_id"), "fred_cpiaucsl", "duplicate series ids"),
        (("fred", "series", 1, "fred_id"), "CPIAUCSL", "duplicate FRED ids"),
        (("shiller", "series", 3, "follows"), "fred_missing", "unknown FRED series"),
        (("fred", "series", 0, "publication_lag_days", "value"), -1, "cannot be negative"),
        (("french", "files", 0, "columns"), ["SMB"], "market_return needs"),
        (("french", "files", 0, "frequency"), "quarterly", "daily or monthly"),
        (("silver", "max_row_loss_fraction", "value"), 1, r"\[0, 1\)"),
        (("fred", "no_vintage_errors", "value"), [], "at least one phrase"),
        (("fred", "no_vintage_errors", "value"), [" "], "at least one phrase"),
        (("ecb", "datasets", 0, "series", 0, "series_id"), "ECB-YC", "String should match"),
        (
            ("fred", "series", 0, "publication_lag_days", "source_url"),
            "http://x",
            "String should match",
        ),
    ],
)
def test_inconsistent_configuration_is_refused(
    path: tuple[str | int, ...], value: Any, message: str
) -> None:
    with pytest.raises(ValidationError, match=message):
        Sources.model_validate(edited(path, value))


@pytest.mark.parametrize(
    ("header", "key"),
    [
        ("Dec 2", "dec2"),
        ("2-Dec", "dec2"),
        ("9-Dec", "dec9"),
        (" dec   2 ", "dec2"),
        ("Lo 10", "lo10"),
        ("Hi 10", "hi10"),
        ("<= 0", "le0"),
        ("Qnt 2", "qnt2"),
        ("Med 40", "med40"),
        ("Mkt-RF", "mkt_rf"),
        ("Agric", "agric"),
    ],
)
def test_column_key_joins_a_word_and_its_number(header: str, key: str) -> None:
    assert column_key(header) == key


@pytest.mark.parametrize("header", ["", "Hi/10", "Hi (10)", "Lo 10%", "-Dec", "Dec 2-"])
def test_column_key_refuses_unknown_headers(header: str) -> None:
    assert column_key(header) is None


@pytest.mark.parametrize(
    ("title", "key"),
    [
        ("Average Value Weight Returns -- Monthly", "value weight returns -- monthly"),
        ("Average Value Weighted Returns -- Daily", "value weight returns -- daily"),
        ("  VALUE   Weighted returns -- Monthly ", "value weight returns -- monthly"),
        ("Average Equal Weight Returns -- Monthly", "equal weight returns -- monthly"),
    ],
)
def test_title_key_ignores_case_spaces_weighted_and_average(title: str, key: str) -> None:
    assert title_key(title) == key


def test_size_series_ids_use_one_word_per_decile() -> None:
    files = [f for f in load_sources().french.files if f.dataset == "size"]
    assert {f.frequency.value for f in files} == {"daily", "monthly"}
    for file in files:
        suffix = file.frequency.value[0]
        assert file.series_ids() == tuple(
            f"kf_size_{key}_{suffix}"
            for key in ["lo10", *(f"dec{n}" for n in range(2, 10)), "hi10"]
        )


@pytest.mark.parametrize("columns", [["Mkt-RF", "Mkt RF"], ["Dec 2", "2-Dec"], ["Hi/10"]])
def test_french_columns_must_have_distinct_known_headers(columns: list[str]) -> None:
    raw = edited(("french", "files", 4, "columns"), columns)
    with pytest.raises(ValidationError, match="known header form and distinct"):
        Sources.model_validate(raw)
