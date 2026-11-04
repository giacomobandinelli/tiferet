"""DefiLlama stablecoin adapter (plan §5.1; decisions 0013, 0014, 0017).

The circulating market cap in US dollars of each configured stablecoin, from the free API on
https://stablecoins.llama.fi. An id is first confirmed with `GET /stablecoin/{id}`, whose
top-level `symbol` must equal the configured one, else the run is refused. The history is
`GET /stablecoincharts/all?stablecoin={id}`: a JSON list of items, each with a date in unix
seconds (a string) at 00:00 UTC and `totalCirculating.peggedUSD`, the circulating amount in
pegged US dollars. The keys are configuration (`defillama.layout`, read from the owner's
`--inspect` run); `--inspect` prints the item keys, the symbol check and the row count,
never a value.

The stored value is the circulating supply valued at the $1 peg, not at the market price.
A value for UTC day d is known at 00:00 UTC on d + 1 plus `publication_lag_hours`, an
approximation. DefiLlama's terms allow personal, non-commercial use and forbid republishing
the data; raw values are never published (decision 0016).

Smoke: `uv run python -m tiferet.data.defillama --smoke`.
"""

import json
import math
from typing import Any

import pandas as pd

from tiferet.data.adapter import Batch, combine, json_object, stamped_utc_days
from tiferet.data.cli import Inspection
from tiferet.data.cli import main as run_cli
from tiferet.data.errors import SourceFormatError
from tiferet.data.fetch import Download, Fetcher, FetchError
from tiferet.data.source_config import DefiLlama, DefiLlamaLayout, Sources, Stablecoin

SOURCE = "defillama"
INFO = "/stablecoin"
CHART = "/stablecoincharts/all"
SECONDS_PER_DAY = 86_400


def info_url(config: DefiLlama, coin: Stablecoin) -> str:
    return f"{config.api_url.value}{INFO}/{coin.llama_id}"


def chart_url(config: DefiLlama) -> str:
    return config.api_url.value + CHART


def chart_params(coin: Stablecoin) -> dict[str, str]:
    return {"stablecoin": coin.llama_id}


def check_symbol(content: bytes, coin: Stablecoin, layout: DefiLlamaLayout) -> bool:
    """Whether the top-level symbol of `/stablecoin/{id}` equals the configured one."""
    return json_object(content, SOURCE, coin.symbol).get(layout.symbol_key) == coin.symbol


def confirm(fetcher: Fetcher, config: DefiLlama, coin: Stablecoin) -> None:
    """Refuse an id whose symbol is not the configured one."""
    content = fetcher.get(info_url(config, coin)).content
    if not check_symbol(content, coin, config.layout.value):
        raise SourceFormatError(
            SOURCE, f"{coin.symbol}: id {coin.llama_id} is not {coin.symbol}; check the id"
        )


def chart_items(content: bytes, item: str) -> list[Any]:
    try:
        document = json.loads(content)
    except ValueError:
        raise SourceFormatError(SOURCE, f"{item}: the answer is not JSON") from None
    if not isinstance(document, list):
        raise SourceFormatError(SOURCE, f"{item}: the answer is not a JSON list")
    return document


def _date(row: dict[str, Any], layout: DefiLlamaLayout, item: str) -> int:
    raw = row.get(layout.date_key)
    if isinstance(raw, str) and raw.isdigit():
        raw = int(raw)
    if isinstance(raw, bool) or not isinstance(raw, int):
        raise SourceFormatError(SOURCE, f"{item}: {layout.date_key!r} is not unix seconds")
    if raw % SECONDS_PER_DAY:
        raise SourceFormatError(SOURCE, f"{item}: a date is not at 00:00 UTC")
    return raw


def _amount(row: dict[str, Any], layout: DefiLlamaLayout, item: str) -> float:
    node: object = row
    for key in layout.value_path:
        if not isinstance(node, dict) or key not in node:
            raise SourceFormatError(SOURCE, f"{item}: an item lacks {'.'.join(layout.value_path)}")
        node = node[key]
    if isinstance(node, bool) or not isinstance(node, int | float):
        raise SourceFormatError(SOURCE, f"{item}: an amount is not a number")
    if not math.isfinite(node) or node < 0:
        raise SourceFormatError(SOURCE, f"{item}: an amount is negative or not finite")
    return float(node)


def parse(content: bytes, coin: Stablecoin, layout: DefiLlamaLayout) -> pd.Series:
    """The circulating amount in pegged US dollars by naive UTC day."""
    item = coin.symbol
    days, amounts = [], []
    for row in chart_items(content, item):
        if not isinstance(row, dict):
            raise SourceFormatError(SOURCE, f"{item}: a chart item is not an object")
        days.append(_date(row, layout, item))
        amounts.append(_amount(row, layout, item))
    index = pd.DatetimeIndex(pd.to_datetime(pd.Series(days, dtype="int64"), unit="s"))
    if not index.is_monotonic_increasing or index.has_duplicates:
        raise SourceFormatError(SOURCE, f"{item}: dates are not unique and increasing")
    return pd.Series(amounts, index=index.rename("obs_date"), dtype=float)


def collect(fetcher: Fetcher, sources: Sources, *, smoke: bool) -> Batch:
    config = sources.defillama
    downloads: list[Download] = []
    frames: list[pd.DataFrame] = []
    for coin in config.stablecoins.value:
        confirm(fetcher, config, coin)
        result = fetcher.get(chart_url(config), chart_params(coin))
        values = parse(result.content, coin, config.layout.value)
        frames.append(stamped_utc_days(coin.series_id, values, config.publication_lag_hours.value))
        downloads.append(result)
    expected = tuple(c.series_id for c in config.stablecoins.value)
    return Batch(SOURCE, tuple(downloads), combine(frames), expected)


def describe(items: list[Any]) -> list[str]:
    """Item keys, nested key paths and the row count; never a value or a date."""
    objects = [r for r in items if isinstance(r, dict)]
    keys = sorted({k for r in objects for k in r})
    nested = sorted(
        {f"{k}.{n}" for r in objects for k, v in r.items() if isinstance(v, dict) for n in v}
    )
    lines = [f"  rows: {len(items)}, not objects: {len(items) - len(objects)}"]
    lines.append(f"  item keys: {', '.join(keys)}")
    if nested:
        lines.append(f"  nested keys: {', '.join(nested)}")
    return lines


def inspect(fetcher: Fetcher, sources: Sources) -> Inspection:
    config = sources.defillama
    layout = config.layout.value
    lines: list[str] = []
    failures = 0
    for coin in config.stablecoins.value:
        try:
            info = fetcher.get(info_url(config, coin)).content
            same = check_symbol(info, coin, layout)
            lines.append(
                f"{coin.symbol} (id {coin.llama_id}): symbol matches: {'yes' if same else 'no'}"
            )
            failures += not same
            items = chart_items(
                fetcher.get(chart_url(config), chart_params(coin)).content, coin.symbol
            )
        except (FetchError, SourceFormatError) as error:
            lines.append(f"{coin.symbol}: {error}")
            failures += 1
            continue
        lines += describe(items)
    return Inspection(lines, failures)


if __name__ == "__main__":
    raise SystemExit(run_cli(SOURCE, collect, inspect=inspect))
