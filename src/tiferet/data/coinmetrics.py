"""Coin Metrics Community adapter (plan §5.1; decisions 0013, 0014, 0017).

Daily price, market capitalization and reported spot trading volume in US dollars for the
configured assets, from the keyless community API: `GET /timeseries/asset-metrics`, one
request per asset and configured series, paged strictly by `next_page_token`. Each row holds
the asset, a `time` at 00:00 UTC that names the day, and the metric's value as a decimal
string or null; null values are dropped.

Which series an asset has is configuration (`coinmetrics.assets`, from the owner's
`--inspect` run): a few assets have no free market cap, so no cap series is configured for
them and none is derived. A configured metric that the community tier does not serve is
answered with HTTP 403 and error type `coinmetrics.forbidden_error_type`; it is then empty,
and ingestion refuses an empty configured series by name. Every other error stays an error.

A value for UTC day d is known at 00:00 UTC on d + 1 plus `publication_lag_hours`, an
approximation; Coin Metrics revises values in place, so the stored value is the latest
download's. Smoke runs stop at the development end, so they request no holdout data.

Smoke: `uv run python -m tiferet.data.coinmetrics --smoke`.
"""

import math
import re
from collections.abc import Sequence
from typing import Any

import pandas as pd

from tiferet.data.adapter import Batch, combine, json_object, stamped_utc_days
from tiferet.data.cli import Inspection
from tiferet.data.cli import main as run_cli
from tiferet.data.errors import SourceFormatError
from tiferet.data.fetch import Download, Fetcher, FetchError
from tiferet.data.pit import DEVELOPMENT_END
from tiferet.data.source_config import CoinMetrics, CoinMetricsAsset, Sources
from tiferet.data.store import HOLDOUT_START

SOURCE = "coinmetrics"
TIMESERIES = "/timeseries/asset-metrics"
CATALOG = "/catalog-v2/asset-metrics"
PAGE_SIZE = "10000"
"""Largest page the API allows."""
MAX_PAGES = 1000
SMOKE_END = DEVELOPMENT_END.date().isoformat()
TIME = re.compile(r"^(\d{4}-\d{2}-\d{2})T00:00:00(?:\.0{1,9})?Z$")
NUMBER = re.compile(r"^-?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?$")
FIRST_DATE_BEFORE = HOLDOUT_START.tz_convert("UTC").tz_localize(None)
"""`--inspect` prints a first date only if it is before this moment."""


def _page(content: bytes, item: str) -> tuple[list[Any], str | None]:
    """The `data` list of one page and its next-page token, with the structure checked."""
    page = json_object(content, SOURCE, item)
    data = page.get("data")
    if not isinstance(data, list):
        raise SourceFormatError(SOURCE, f"{item}: response lacks a data list")
    token = page.get("next_page_token")
    if token is not None and (not isinstance(token, str) or not token):
        raise SourceFormatError(SOURCE, f"{item}: next_page_token is not a non-empty string")
    return data, token


def pages(
    fetcher: Fetcher, url: str, params: dict[str, str], item: str
) -> tuple[list[Download], list[Any]]:
    """Every page of one query, following `next_page_token` until a page has none, with the
    rows of all pages in order.

    A token seen before, or more than `MAX_PAGES` pages, is a format error.
    """
    downloads: list[Download] = []
    rows: list[Any] = []
    seen: set[str] = set()
    token: str | None = None
    while True:
        query = params if token is None else {**params, "next_page_token": token}
        result = fetcher.get(url, query)
        downloads.append(result)
        data, token = _page(result.content, item)
        rows += data
        if token is None:
            return downloads, rows
        if token in seen:
            raise SourceFormatError(SOURCE, f"{item}: next_page_token repeats")
        if len(downloads) >= MAX_PAGES:
            raise SourceFormatError(SOURCE, f"{item}: more than {MAX_PAGES} pages")
        seen.add(token)


def is_forbidden(error: FetchError, config: CoinMetrics) -> bool:
    """True when the API says the metric is not available without a key."""
    kind = (error.detail or "").split(":", 1)[0].strip()
    return error.status == 403 and kind == config.forbidden_error_type.value


def _value(raw: object, item: str) -> float | None:
    if raw is None:
        return None
    if not isinstance(raw, str) or not NUMBER.fullmatch(raw):
        raise SourceFormatError(SOURCE, f"{item}: a value is not a decimal string or null")
    value = float(raw)
    if not math.isfinite(value):
        raise SourceFormatError(SOURCE, f"{item}: a value is not finite")
    return value


def parse(data: Sequence[Any], asset: str, metric: str) -> pd.Series:
    """The metric's values by naive UTC day from the rows of every page, nulls dropped."""
    item = f"{asset} {metric}"
    days: list[str] = []
    values: list[float | None] = []
    for row in data:
        if not isinstance(row, dict) or row.get("asset") != asset:
            raise SourceFormatError(SOURCE, f"{item}: a row is not for asset {asset}")
        time = row.get("time")
        match = TIME.fullmatch(time) if isinstance(time, str) else None
        if match is None:
            raise SourceFormatError(SOURCE, f"{item}: a time is not 00:00 UTC of a day")
        if metric not in row:
            raise SourceFormatError(SOURCE, f"{item}: a row lacks the metric")
        days.append(match.group(1))
        values.append(_value(row[metric], item))
    try:
        dates = pd.to_datetime(pd.Series(days, dtype=str), format="%Y-%m-%d")
    except ValueError:
        raise SourceFormatError(SOURCE, f"{item}: a time is not a valid date") from None
    if not dates.is_monotonic_increasing or dates.duplicated().any():
        raise SourceFormatError(SOURCE, f"{item}: days are not unique and increasing")
    series = pd.Series(values, index=pd.DatetimeIndex(dates, name="obs_date"), dtype=float)
    return series.dropna()


def metric_values(
    fetcher: Fetcher, config: CoinMetrics, asset: str, metric: str, *, smoke: bool
) -> tuple[list[Download], pd.Series | None]:
    """Every daily value of one metric, or None when the community tier does not serve it."""
    params = {
        "assets": asset,
        "metrics": metric,
        "frequency": "1d",
        "page_size": PAGE_SIZE,
        "paging_from": "start",
    }
    if smoke:
        params["end_time"] = SMOKE_END
    try:
        downloads, rows = pages(
            fetcher, config.api_url.value + TIMESERIES, params, f"{asset} {metric}"
        )
    except FetchError as error:
        if is_forbidden(error, config):
            return [], None
        raise
    return downloads, parse(rows, asset, metric)


def asset_rows(
    fetcher: Fetcher, config: CoinMetrics, entry: CoinMetricsAsset, *, smoke: bool
) -> tuple[list[Download], list[pd.DataFrame]]:
    """The configured series of one asset. A metric the community tier does not serve gives
    an empty series, which ingestion refuses by name."""
    lag = config.publication_lag_hours.value
    downloads: list[Download] = []
    frames: list[pd.DataFrame] = []
    for kind in entry.series:
        got, values = metric_values(
            fetcher, config, entry.asset, config.metrics.name(kind), smoke=smoke
        )
        downloads += got
        empty = pd.Series(dtype=float, index=pd.DatetimeIndex([], name="obs_date"))
        frames.append(
            stamped_utc_days(entry.series_id(kind), empty if values is None else values, lag)
        )
    return downloads, frames


def collect(fetcher: Fetcher, sources: Sources, *, smoke: bool) -> Batch:
    config = sources.coinmetrics
    downloads: list[Download] = []
    frames: list[pd.DataFrame] = []
    for entry in config.assets.value:
        got, stamped_frames = asset_rows(fetcher, config, entry, smoke=smoke)
        downloads += got
        frames += stamped_frames
    return Batch(SOURCE, tuple(downloads), combine(frames), config.series_ids())


def _first_date(entry: dict[str, Any]) -> str:
    """ ", from <date>" when `min_time` is a day before the holdout; else nothing."""
    raw = entry.get("min_time")
    match = TIME.fullmatch(raw) if isinstance(raw, str) else None
    if match is None or pd.Timestamp(match.group(1)) >= FIRST_DATE_BEFORE:
        return ""
    return f", from {match.group(1)}"


def _daily(metrics: object, metric: str) -> dict[str, Any] | None:
    """The catalog's 1d frequency entry of `metric`, or None."""
    if not isinstance(metrics, list):
        return None
    for entry in metrics:
        if isinstance(entry, dict) and entry.get("metric") == metric:
            for frequency in entry.get("frequencies") or []:
                if isinstance(frequency, dict) and frequency.get("frequency") == "1d":
                    return frequency
    return None


def inspect(fetcher: Fetcher, sources: Sources) -> Inspection:
    """Per asset and metric, whether the catalog lists a daily series and flags it community,
    and its first date if before 2023. Never a later date and never a value."""
    config = sources.coinmetrics
    names = (config.metrics.price, config.metrics.cap, config.metrics.volume)
    lines: list[str] = []
    failures = 0
    for asset in (entry.asset for entry in config.assets.value):
        params = {"assets": asset, "page_size": PAGE_SIZE}
        try:
            _, entries = pages(fetcher, config.api_url.value + CATALOG, params, f"catalog {asset}")
        except (FetchError, SourceFormatError) as error:
            lines.append(f"{asset}: {error}")
            failures += 1
            continue
        found = [e for e in entries if isinstance(e, dict) and e.get("asset") == asset]
        metrics = [m for e in found if isinstance(e.get("metrics"), list) for m in e["metrics"]]
        if not found:
            lines.append(f"{asset}: not in the catalog")
            failures += 1
            continue
        for metric in (*names, *config.probe_metrics):
            daily = _daily(metrics, metric)
            if daily is None:
                lines.append(f"{asset} {metric}: 1d not listed, community no")
                continue
            community = "yes" if daily.get("community") is True else "no"
            lines.append(f"{asset} {metric}: 1d listed, community {community}{_first_date(daily)}")
    return Inspection(lines, failures)


if __name__ == "__main__":
    raise SystemExit(run_cli(SOURCE, collect, inspect=inspect))
