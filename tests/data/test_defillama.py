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

API = "https://stablecoins.llama.fi"
INFO = API + "/stablecoin/"
CHART = API + "/stablecoincharts/all"
FILES = {"1": "usdt.json", "2": "usdc.json"}
INFOS = {"1": "info_usdt.json", "2": "info_usdc.json"}


def fetcher(
    replace: dict[str, bytes] | None = None, info: dict[str, bytes] | None = None
) -> FakeFetcher:
    def respond(url: str, params: dict[str, str]) -> bytes:
        if url.startswith(INFO):
            assert not params
            llama_id = url.removeprefix(INFO)
            if info and llama_id in info:
                return info[llama_id]
            return fixture("defillama", INFOS[llama_id])
        assert url == CHART and set(params) == {"stablecoin"}
        llama_id = params["stablecoin"]
        if replace and llama_id in replace:
            return replace[llama_id]
        return fixture("defillama", FILES[llama_id])

    return FakeFetcher(respond)


def run(tmp_path: Path, fake: FakeFetcher | None = None) -> Batch:
    batch = defillama.collect(fake or fetcher(), load_sources(), smoke=False)
    ingest(batch, silver_roots(tmp_path), tmp_path / "bronze", max_row_loss=0.001)
    return batch


def document(name: str = "usdt.json") -> list[Any]:
    parsed: list[Any] = json.loads(fixture("defillama", name))
    return parsed


def encoded(changed: Any) -> bytes:
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


def test_the_symbol_is_confirmed_before_the_history_is_requested() -> None:
    fake = fetcher()
    defillama.collect(fake, load_sources(), smoke=False)
    assert [u for u, _ in fake.calls] == [INFO + "1", CHART, INFO + "2", CHART]
    assert fake.calls[1][1] == {"stablecoin": "1"}


def test_the_total_circulating_in_pegged_dollars_is_stored_not_the_usd_value() -> None:
    silver = defillama.collect(fetcher(), load_sources(), smoke=False).silver
    usdt = silver[silver["series_id"] == "defillama_usdt_cap_usd"].set_index("obs_date")["value"]
    assert usdt.iloc[1] == pytest.approx(66210000000)  # the fixture's USD value differs


def test_an_id_with_another_symbol_is_refused_before_any_history(tmp_path: Path) -> None:
    fake = fetcher(info={"2": fixture("defillama", "info_usdt.json")})
    with pytest.raises(SourceFormatError, match=r"defillama: USDC: id 2 is not USDC"):
        run(tmp_path, fake)
    assert [p for _, p in fake.calls if p] == [{"stablecoin": "1"}]
    assert not (tmp_path / "bronze").exists()


def test_a_date_written_as_a_number_is_accepted() -> None:
    changed = document()
    changed[0]["date"] = int(changed[0]["date"])
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
        (b"{}", "not a JSON list"),
        (variant((0,), 1), "not an object"),
        (variant((0, "date"), "1672012801"), "not at 00:00 UTC"),
        (variant((0, "date"), "26.12.2022"), "not unix seconds"),
        (variant((0, "date"), "1672444800"), "not unique and increasing"),
        (variant((0, "totalCirculating"), {"peggedEUR": 1}), "lacks totalCirculating.peggedUSD"),
        (variant((0, "totalCirculating", "peggedUSD"), -1), "negative or not finite"),
        (variant((0, "totalCirculating", "peggedUSD"), "1"), "not a number"),
    ],
    ids=["not-json", "not-list", "item", "midnight", "date", "order", "path", "negative", "text"],
)
def test_format_errors_name_the_stablecoin_and_write_nothing(
    tmp_path: Path, content: bytes, message: str
) -> None:
    run(tmp_path)
    before = snapshot(tmp_path)
    with pytest.raises(SourceFormatError, match=f"defillama: USDT: .*{message}"):
        run(tmp_path, fetcher({"1": content}))
    assert snapshot(tmp_path) == before


def test_inspect_prints_keys_symbol_check_and_row_count_only() -> None:
    inspection = defillama.inspect(fetcher(), load_sources())
    assert inspection.failures == 0
    lines = inspection.lines
    assert lines[:4] == [
        "USDT (id 1): symbol matches: yes",
        "  rows: 6, not objects: 0",
        "  item keys: date, totalCirculating, totalCirculatingUSD, totalUnreleased",
        "  nested keys: totalCirculating.peggedUSD, totalCirculatingUSD.peggedUSD, "
        "totalUnreleased.peggedUSD",
    ]
    text = "\n".join(lines)
    numbers = re.findall(r"\d{6,}(?:\.\d+)?", fixture("defillama", "usdt.json").decode())
    assert not [n for n in numbers if n in text]
    assert not re.search(r"20\d\d", text)


def test_inspect_flags_a_wrong_symbol_and_an_unusable_answer() -> None:
    inspection = defillama.inspect(
        fetcher({"2": b"{}"}, info={"1": fixture("defillama", "info_usdc.json")}), load_sources()
    )
    assert inspection.failures == 2
    assert "USDT (id 1): symbol matches: no" in inspection.lines
    assert any(line.startswith("USDC: ") and "not a JSON list" in line for line in inspection.lines)
