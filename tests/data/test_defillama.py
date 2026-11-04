"""Tests for the DefiLlama stablecoin adapter on JSON fixtures (decisions 0013, 0017)."""

import json
import re
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from data.adapter_support import (
    FakeFetcher,
    assert_partitioned,
    fixture,
    known_at,
    production_view,
    research_view,
    silver_roots,
    snapshot,
    utc,
)
from tiferet.data import defillama
from tiferet.data.adapter import Batch, ingest
from tiferet.data.errors import SourceFormatError
from tiferet.data.source_config import load_sources

API = "https://api.llama.fi/stablecoin/"
FILES = {"1": "usdt.json", "2": "usdc.json"}


def fetcher(replace: dict[str, bytes] | None = None) -> FakeFetcher:
    def respond(url: str, params: dict[str, str]) -> bytes:
        assert url.startswith(API) and not params
        llama_id = url.removeprefix(API)
        if replace and llama_id in replace:
            return replace[llama_id]
        return fixture("defillama", FILES[llama_id])

    return FakeFetcher(respond)


def run(tmp_path: Path, fake: FakeFetcher | None = None) -> Batch:
    batch = defillama.collect(fake or fetcher(), load_sources(), smoke=False)
    ingest(batch, silver_roots(tmp_path), tmp_path / "bronze", max_row_loss=0.001)
    return batch


def document(name: str = "usdt.json") -> dict[str, Any]:
    parsed: dict[str, Any] = json.loads(fixture("defillama", name))
    return parsed


def encoded(changed: dict[str, Any]) -> bytes:
    return json.dumps(changed).encode()


def test_circulating_amounts_by_day() -> None:
    silver = defillama.collect(fetcher(), load_sources(), smoke=False).silver
    usdt = silver[silver["series_id"] == "defillama_usdt_cap_usd"].set_index("obs_date")["value"]
    assert len(usdt) == 6
    assert usdt.index[0] == pd.Timestamp("2022-12-26")
    assert usdt.iloc[0] == pytest.approx(66200000000.5)
    usdc = silver[silver["series_id"] == "defillama_usdc_cap_usd"]
    assert usdc["obs_date"].min() == pd.Timestamp("2022-12-28")
    assert set(silver["vintage_kind"]) == {"approximated"}


def test_known_the_day_after_plus_the_lag() -> None:
    silver = defillama.collect(fetcher(), load_sources(), smoke=False).silver
    usdt = silver[silver["series_id"] == "defillama_usdt_cap_usd"]
    assert known_at(usdt, "2022-12-26") == [utc("2022-12-28 00:00")]
    assert known_at(usdt, "2022-12-31") == [utc("2023-01-02 00:00")]


def test_output_passes_pit_get_and_respects_the_holdout_boundary(tmp_path: Path) -> None:
    run(tmp_path)
    roots = silver_roots(tmp_path)
    assert_partitioned(roots)
    assert len(research_view(roots, "defillama_usdt_cap_usd")) == 4
    assert len(production_view(roots, "defillama_usdt_cap_usd")) == 6
    assert len(research_view(roots, "defillama_usdc_cap_usd")) == 2


def test_a_date_written_as_digits_is_accepted() -> None:
    changed = document()
    changed["tokens"][0]["date"] = str(changed["tokens"][0]["date"])
    silver = defillama.collect(fetcher({"1": encoded(changed)}), load_sources(), smoke=False)
    assert len(silver.silver[silver.silver["series_id"] == "defillama_usdt_cap_usd"]) == 6


def variant(path: tuple[str | int, ...], value: Any) -> bytes:
    changed: Any = document()
    node = changed
    for step in path[:-1]:
        node = node[step]
    node[path[-1]] = value
    return encoded(changed)


@pytest.mark.parametrize(
    ("content", "message"),
    [
        (b"<html>", "not JSON"),
        (b"[]", "not a JSON object"),
        (variant(("symbol",), "USDC"), "not for USDT"),
        (variant(("tokens",), {}), "'tokens' is not a list"),
        (variant(("tokens", 0), 1), "not an object"),
        (variant(("tokens", 0, "date"), 1672012801), "not at 00:00 UTC"),
        (variant(("tokens", 0, "date"), "26.12.2022"), "not unix seconds"),
        (variant(("tokens", 0, "date"), 1672444800), "not unique and increasing"),
        (variant(("tokens", 0, "circulating"), {"peggedEUR": 1}), "lacks circulating.peggedUSD"),
        (variant(("tokens", 0, "circulating", "peggedUSD"), -1), "negative or not finite"),
        (variant(("tokens", 0, "circulating", "peggedUSD"), "1"), "not a number"),
    ],
    ids=[
        "not-json",
        "not-object",
        "symbol",
        "history",
        "item",
        "midnight",
        "date",
        "order",
        "path",
        "negative",
        "text",
    ],
)
def test_format_errors_name_the_stablecoin_and_write_nothing(
    tmp_path: Path, content: bytes, message: str
) -> None:
    run(tmp_path)
    before = snapshot(tmp_path)
    with pytest.raises(SourceFormatError, match=f"defillama: USDT: .*{message}"):
        run(tmp_path, fetcher({"1": content}))
    assert snapshot(tmp_path) == before


def test_inspect_prints_keys_counts_and_an_early_first_date_only() -> None:
    inspection = defillama.inspect(fetcher(), load_sources())
    assert inspection.failures == 0
    lines = inspection.lines
    assert "USDT (id 1): symbol matches: yes" in lines
    assert "  top-level keys: id, name, pegType, symbol, tokens" in lines
    assert "  tokens: 6 items, keys circulating, date" in lines
    assert "  dates not at 00:00 UTC: 0, not unix seconds: 0" in lines
    assert "  first date: 2022-12-26" in lines
    text = "\n".join(lines)
    numbers = re.findall(r"\d{6,}(?:\.\d+)?", fixture("defillama", "usdt.json").decode())
    assert not [n for n in numbers if n in text]


def test_inspect_hides_a_first_date_after_2022_and_flags_a_wrong_symbol() -> None:
    changed = document("usdc.json")
    changed["tokens"] = [{"date": 1704067200, "circulating": {"peggedUSD": 5}}]
    inspection = defillama.inspect(fetcher({"1": encoded(changed)}), load_sources())
    assert inspection.failures == 1
    assert "USDT (id 1): symbol matches: no" in inspection.lines
    assert not any("2024" in line or "first date" in line for line in inspection.lines[:5])
