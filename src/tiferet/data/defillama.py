"""DefiLlama stablecoin adapter (plan §5.1; decisions 0013, 0014, 0017).

The circulating market cap in US dollars of each configured stablecoin, from the free API's
`GET /stablecoin/{id}`. The answer is a JSON object whose `symbol` must equal the configured
one, holding a history list; each item has a date in unix seconds at 00:00 UTC and the
circulating amount in pegged US dollars. The keys are configuration (`defillama.layout`),
because the documentation gives no schema; `--inspect` prints them without values.

The stored value is the circulating supply valued at the $1 peg, not at the market price.
A value for UTC day d is known at 00:00 UTC on d + 1 plus `publication_lag_hours`, an
approximation. DefiLlama's terms allow personal, non-commercial use and forbid republishing
the data; raw values are never published (decision 0016).

Smoke: `uv run python -m tiferet.data.defillama --smoke`.
"""

import math
from typing import Any

import pandas as pd

from tiferet.data.adapter import Batch, combine, json_object, stamped_utc_days
from tiferet.data.cli import Inspection
from tiferet.data.cli import main as run_cli
from tiferet.data.errors import SourceFormatError
from tiferet.data.fetch import Download, Fetcher, FetchError
from tiferet.data.source_config import DefiLlama, DefiLlamaLayout, Sources, Stablecoin
from tiferet.data.store import HOLDOUT_START

SOURCE = "defillama"
SECONDS_PER_DAY = 86_400
FIRST_DATE_BEFORE = int(HOLDOUT_START.tz_convert("UTC").timestamp())
"""`--inspect` prints a first date only if it is before this moment."""


def url(config: DefiLlama, coin: Stablecoin) -> str:
    return f"{config.api_url}/stablecoin/{coin.llama_id}"


def history(document: dict[str, Any], layout: DefiLlamaLayout, item: str) -> list[Any]:
    rows = document.get(layout.history_key)
    if not isinstance(rows, list):
        raise SourceFormatError(SOURCE, f"{item}: {layout.history_key!r} is not a list")
    return rows


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
    document = json_object(content, SOURCE, item)
    if document.get(layout.symbol_key) != coin.symbol:
        raise SourceFormatError(SOURCE, f"{item}: the answer is not for {coin.symbol}")
    days, amounts = [], []
    for row in history(document, layout, item):
        if not isinstance(row, dict):
            raise SourceFormatError(SOURCE, f"{item}: a history item is not an object")
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
        result = fetcher.get(url(config, coin))
        values = parse(result.content, coin, config.layout.value)
        frames.append(stamped_utc_days(coin.series_id, values, config.publication_lag_hours.value))
        downloads.append(result)
    expected = tuple(c.series_id for c in config.stablecoins.value)
    return Batch(SOURCE, tuple(downloads), combine(frames), expected)


def describe(document: dict[str, Any], layout: DefiLlamaLayout) -> list[str]:
    """Keys, counts and the first date if before 2023; never a value or a later date."""
    lines = [f"  top-level keys: {', '.join(sorted(document))}"]
    rows = document.get(layout.history_key)
    if not isinstance(rows, list):
        return [*lines, f"  {layout.history_key}: not a list"]
    items = [r for r in rows if isinstance(r, dict)]
    keys = sorted({k for r in items for k in r})
    lines += [f"  {layout.history_key}: {len(rows)} items, keys {', '.join(keys)}"]
    dates = [r.get(layout.date_key) for r in items]
    seconds = [int(d) for d in dates if isinstance(d, int | str) and str(d).isdigit()]
    off_midnight = sum(1 for s in seconds if s % SECONDS_PER_DAY)
    lines += [
        f"  dates not at 00:00 UTC: {off_midnight}, not unix seconds: {len(rows) - len(seconds)}"
    ]
    if seconds and min(seconds) < FIRST_DATE_BEFORE:
        first = pd.Timestamp(min(seconds), unit="s").date().isoformat()
        lines += [f"  first date: {first}"]
    return lines


def inspect(fetcher: Fetcher, sources: Sources) -> Inspection:
    config = sources.defillama
    layout = config.layout.value
    lines: list[str] = []
    failures = 0
    for coin in config.stablecoins.value:
        try:
            document = json_object(fetcher.get(url(config, coin)).content, SOURCE, coin.symbol)
        except (FetchError, SourceFormatError) as error:
            lines.append(f"{coin.symbol}: {error}")
            failures += 1
            continue
        same = document.get(layout.symbol_key) == coin.symbol
        lines.append(
            f"{coin.symbol} (id {coin.llama_id}): symbol matches: {'yes' if same else 'no'}"
        )
        failures += not same
        lines += describe(document, layout)
    return Inspection(lines, failures)


if __name__ == "__main__":
    raise SystemExit(run_cli(SOURCE, collect, inspect=inspect))
