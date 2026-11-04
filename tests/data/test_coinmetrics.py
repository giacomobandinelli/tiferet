"""Tests for the Coin Metrics Community adapter on JSON fixtures (decisions 0013, 0017)."""

import io
import json
import re
import urllib.error
import urllib.request
from email.message import Message
from pathlib import Path
from types import TracebackType

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
from tiferet.data import coinmetrics
from tiferet.data.adapter import Batch, ingest
from tiferet.data.errors import DataLossError, SourceFormatError
from tiferet.data.fetch import FetchError, HttpFetcher
from tiferet.data.source_config import (
    CoinMetricsAsset,
    CoinMetricsSeries,
    Sources,
    load_sources,
)

SERIES_PRICE, SERIES_CAP, SERIES_VOLUME = CoinMetricsSeries
VOLUME_METRIC = "volume_reported_spot_usd_1d"
API = "https://community-api.coinmetrics.io/v4"
SERIES = API + coinmetrics.TIMESERIES
CATALOG = API + coinmetrics.CATALOG
FORBIDDEN_TEXT = json.loads(fixture("coinmetrics", "error_forbidden.json"))["error"]["message"]
FILES = {
    ("btc", "PriceUSD", None): "btc_price_page1.json",
    ("btc", "PriceUSD", "token-a"): "btc_price_page2.json",
    ("btc", "CapMrktCurUSD", None): "btc_cap.json",
    ("eth", "PriceUSD", None): "eth_price.json",
    ("btc", "volume_reported_spot_usd_1d", None): "btc_volume.json",
    ("eth", "volume_reported_spot_usd_1d", None): "eth_volume.json",
}
CATALOGS = {
    ("btc", None): "catalog_btc.json",
    ("btc", "catalog-2"): "catalog_btc_page2.json",
    ("eth", None): "catalog_eth.json",
}

Key = tuple[str, str, str | None]


BTC = CoinMetricsAsset(asset="btc", series=(SERIES_PRICE, SERIES_CAP, SERIES_VOLUME))
ETH = CoinMetricsAsset(asset="eth", series=(SERIES_PRICE, SERIES_VOLUME))
"""Ethereum without a configured cap, like the assets that have no free market cap."""


def sources(assets: tuple[CoinMetricsAsset, ...] = (BTC, ETH)) -> Sources:
    config = load_sources()
    cm = config.coinmetrics
    listed = cm.assets.model_copy(update={"value": assets})
    return config.model_copy(update={"coinmetrics": cm.model_copy(update={"assets": listed})})


def forbidden() -> FetchError:
    detail = f"forbidden: {FORBIDDEN_TEXT}"
    return FetchError(f"HTTP 403 from {SERIES}: {detail}", status=403, detail=detail)


def fetcher(
    replace: dict[Key, bytes] | None = None, forbid: frozenset[tuple[str, str]] = frozenset()
) -> FakeFetcher:
    def respond(url: str, params: dict[str, str]) -> bytes:
        token = params.get("next_page_token")
        if url == CATALOG:
            return fixture("coinmetrics", CATALOGS[(params["assets"], token)])
        assert url == SERIES
        asset, metric = params["assets"], params["metrics"]
        key = (asset, metric, token)
        if replace and key in replace:
            return replace[key]
        if (asset, metric) in forbid:
            raise forbidden()
        return fixture("coinmetrics", FILES[key])

    return FakeFetcher(respond)


def run(tmp_path: Path, fake: FakeFetcher | None = None) -> Batch:
    batch = coinmetrics.collect(fake or fetcher(), sources(), smoke=False)
    ingest(batch, silver_roots(tmp_path), tmp_path / "bronze", max_row_loss=0.001)
    return batch


def values(silver: pd.DataFrame, series_id: str) -> pd.Series:
    rows = silver[silver["series_id"] == series_id]
    return rows.set_index("obs_date")["value"]


def test_price_pages_are_joined_and_nulls_dropped() -> None:
    fake = fetcher()
    silver = coinmetrics.collect(fake, sources(), smoke=False).silver
    price = values(silver, "cm_btc_price_usd")
    assert [d.date().isoformat() for d in price.index] == [
        "2022-12-27",
        "2022-12-28",
        "2022-12-30",
        "2022-12-31",
    ]
    assert price[pd.Timestamp("2022-12-28")] == pytest.approx(16550.25)
    assert set(silver["vintage_kind"]) == {"approximated"}
    first, second = fake.calls[0][1], fake.calls[1][1]
    assert first == {
        "assets": "btc",
        "metrics": "PriceUSD",
        "frequency": "1d",
        "page_size": "10000",
        "paging_from": "start",
    }
    assert second == {**first, "next_page_token": "token-a"}


def test_reported_cap_is_used_where_it_is_free() -> None:
    fake = fetcher()
    silver = coinmetrics.collect(fake, sources(), smoke=False).silver
    assert len(values(silver, "cm_btc_cap_usd")) == 5
    assert ("btc", "SplyCur") not in {(p["assets"], p["metrics"]) for _, p in fake.calls}


def test_volume_is_stored_as_its_own_series() -> None:
    silver = coinmetrics.collect(fetcher(), sources(), smoke=False).silver
    volume = values(silver, "cm_btc_volume_usd")
    assert len(volume) == 4  # the null day is dropped
    assert volume[pd.Timestamp("2022-12-27")] == pytest.approx(9.1e9)
    assert len(values(silver, "cm_eth_volume_usd")) == 3


def test_only_configured_series_are_requested_and_stored() -> None:
    fake = fetcher()
    silver = coinmetrics.collect(fake, sources(), smoke=False).silver
    requested = {(p["assets"], p["metrics"]) for _, p in fake.calls}
    assert ("eth", "CapMrktCurUSD") not in requested
    assert ("btc", "SplyCur") not in requested
    assert "cm_eth_cap_usd" not in set(silver["series_id"])


def test_known_the_day_after_plus_the_lag() -> None:
    silver = coinmetrics.collect(fetcher(), sources(), smoke=False).silver
    cap = silver[silver["series_id"] == "cm_btc_cap_usd"]
    assert known_at(cap, "2022-12-27") == [utc("2022-12-29 00:00")]
    assert known_at(cap, "2022-12-30") == [utc("2023-01-01 00:00")]


def test_output_passes_pit_get_and_respects_the_holdout_boundary(tmp_path: Path) -> None:
    run(tmp_path)
    roots = silver_roots(tmp_path)
    assert_partitioned(roots)
    # 2022-12-29 is known at 00:00 UTC on 2022-12-31; 2022-12-30 at 01:00 Berlin on 1 January.
    assert len(research_view(roots, "cm_btc_cap_usd")) == 3
    assert len(production_view(roots, "cm_btc_cap_usd")) == 5
    assert len(research_view(roots, "cm_eth_volume_usd")) == 3


def test_smoke_requests_stop_at_the_development_end() -> None:
    fake = fetcher()
    coinmetrics.collect(fake, sources(), smoke=True)
    assert {params.get("end_time") for _, params in fake.calls} == {"2022-12-31"}


def test_a_configured_series_that_is_not_served_is_refused_by_name(tmp_path: Path) -> None:
    fake = fetcher(forbid=frozenset({("btc", "CapMrktCurUSD")}))
    batch = coinmetrics.collect(fake, sources(), smoke=False)
    with pytest.raises(DataLossError, match="cm_btc_cap_usd"):
        ingest(batch, silver_roots(tmp_path), tmp_path / "bronze", max_row_loss=0.001)
    assert not (tmp_path / "bronze").exists()


def test_an_asset_without_a_free_cap_is_not_refused_for_it(tmp_path: Path) -> None:
    batch = run(tmp_path)
    assert "cm_eth_cap_usd" not in batch.expected


def test_other_errors_stay_errors() -> None:
    detail = "bad_parameter: Bad parameter 'metrics'."

    def respond(url: str, params: dict[str, str]) -> bytes:
        raise FetchError(f"HTTP 400: {detail}", status=400, detail=detail)

    with pytest.raises(FetchError, match="HTTP 400"):
        coinmetrics.collect(FakeFetcher(respond), sources(), smoke=False)


def test_a_forbidden_answer_with_another_status_is_an_error() -> None:
    error = FetchError("HTTP 401", status=401, detail="forbidden: no")
    assert not coinmetrics.is_forbidden(error, sources().coinmetrics)
    assert coinmetrics.is_forbidden(forbidden(), sources().coinmetrics)


def page(*rows: dict[str, object], token: str | None = None) -> bytes:
    body: dict[str, object] = {"data": list(rows)}
    if token is not None:
        body["next_page_token"] = token
    return json.dumps(body).encode()


def row(time: str = "2022-12-27T00:00:00.000000000Z", **fields: object) -> dict[str, object]:
    return {"asset": "btc", "time": time, "CapMrktCurUSD": "1", **fields}


CAP: Key = ("btc", "CapMrktCurUSD", None)


@pytest.mark.parametrize(
    ("replace", "message"),
    [
        ({("btc", "PriceUSD", "token-a"): page(token="token-a")}, "next_page_token repeats"),
        ({CAP: b"<html>"}, "not JSON"),
        ({CAP: b"[]"}, "not a JSON object"),
        ({CAP: b'{"rows": []}'}, "lacks a data list"),
        ({CAP: page(token="")}, "non-empty string"),
        ({CAP: page(row(asset="eth"))}, "not for asset btc"),
        ({CAP: page(row(time="2022-12-27T12:00:00Z"))}, "00:00 UTC"),
        ({CAP: page(row(time="2022-02-30T00:00:00Z"))}, "not a valid date"),
        ({CAP: page(row(CapMrktCurUSD="1,5"))}, "not a decimal string"),
        ({CAP: page(row(CapMrktCurUSD=1.5))}, "not a decimal string"),
        ({CAP: page({"asset": "btc", "time": "2022-12-27T00:00:00Z"})}, "lacks the metric"),
        ({CAP: page(row(), row())}, "not unique and increasing"),
    ],
    ids=[
        "repeated-token",
        "not-json",
        "not-object",
        "no-data",
        "empty-token",
        "asset",
        "time",
        "date",
        "text",
        "number",
        "metric",
        "repeat",
    ],
)
def test_format_errors_name_the_item_and_write_nothing(
    tmp_path: Path, replace: dict[Key, bytes], message: str
) -> None:
    run(tmp_path)
    before = snapshot(tmp_path)
    with pytest.raises(SourceFormatError, match=f"coinmetrics: btc [A-Za-z]+: .*{message}"):
        run(tmp_path, fetcher(replace))
    assert snapshot(tmp_path) == before


def test_paging_stops_at_the_page_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(coinmetrics, "MAX_PAGES", 3)
    counter = iter(range(100))

    def respond(url: str, params: dict[str, str]) -> bytes:
        return page(token=f"t{next(counter)}")

    with pytest.raises(SourceFormatError, match="more than 3 pages"):
        coinmetrics.collect(FakeFetcher(respond), sources((BTC,)), smoke=False)


class ScriptResponse:
    def __init__(self, body: bytes) -> None:
        self._body = io.BytesIO(body)
        self.headers = Message()

    def read(self, amt: int, /) -> bytes:
        return self._body.read(amt)

    def __enter__(self) -> "ScriptResponse":
        return self

    def __exit__(
        self,
        kind: type[BaseException] | None,
        error: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        return None


class ScriptOpener:
    """Answers each request with the next step of a script."""

    def __init__(self, *steps: bytes | urllib.error.HTTPError) -> None:
        self.steps = list(steps)
        self.urls: list[str] = []

    def open(self, request: urllib.request.Request, *, timeout: float) -> ScriptResponse:
        self.urls.append(request.full_url)
        step = self.steps.pop(0)
        if isinstance(step, urllib.error.HTTPError):
            raise step
        return ScriptResponse(step)


def http_error(code: int, body: bytes = b"", retry_after: str | None = None) -> Exception:
    headers = Message()
    if retry_after is not None:
        headers["Retry-After"] = retry_after
    return urllib.error.HTTPError(SERIES, code, "error", headers, io.BytesIO(body))


def test_429_is_retried_after_its_delay_and_403_leaves_the_series_empty() -> None:
    sleeps: list[float] = []
    opener = ScriptOpener(
        http_error(429, retry_after="5"),  # type: ignore[arg-type]
        fixture("coinmetrics", "eth_price.json"),
        http_error(403, fixture("coinmetrics", "error_forbidden.json")),  # type: ignore[arg-type]
        fixture("coinmetrics", "eth_volume.json"),
    )
    client = HttpFetcher(opener=opener, clock=lambda: 0.0, sleep=sleeps.append, min_interval=0.0)
    asset = CoinMetricsAsset(asset="eth", series=(SERIES_PRICE, SERIES_CAP, SERIES_VOLUME))
    batch = coinmetrics.collect(client, sources((asset,)), smoke=False)
    assert sleeps == [5.0]
    assert len(values(batch.silver, "cm_eth_cap_usd")) == 0
    assert len(values(batch.silver, "cm_eth_volume_usd")) == 3
    assert [re.search(r"metrics=(\w+)", u).group(1) for u in opener.urls] == [  # type: ignore[union-attr]
        "PriceUSD",
        "PriceUSD",
        "CapMrktCurUSD",
        VOLUME_METRIC,
    ]


def test_requests_are_spaced_by_the_configured_interval() -> None:
    sleeps: list[float] = []
    opener = ScriptOpener(
        fixture("coinmetrics", "eth_price.json"),
        fixture("coinmetrics", "eth_volume.json"),
    )
    config = sources((ETH,))
    client = HttpFetcher(
        opener=opener,
        clock=lambda: 0.0,
        sleep=sleeps.append,
        host_intervals=config.host_intervals(),
    )
    coinmetrics.collect(client, config, smoke=False)
    assert sleeps == [0.7]


def fixture_tokens() -> set[str]:
    """Every value and date in the catalog fixtures that inspect must not print."""
    text = "".join(fixture("coinmetrics", n).decode() for n in set(CATALOGS.values()))
    return set(re.findall(r"\d{4}-\d{2}-\d{2}", text)) - {
        "2010-07-18",
        "2015-08-08",
        "2015-07-30",
        "2016-01-04",
    }


def test_inspect_prints_availability_and_early_first_dates_only() -> None:
    fake = fetcher()
    inspection = coinmetrics.inspect(fake, sources())
    assert inspection.failures == 0
    lines = inspection.lines
    assert "btc PriceUSD: 1d listed, community yes, from 2010-07-18" in lines
    assert "btc SplyCur: 1d not listed, community no" in lines
    assert f"btc {VOLUME_METRIC}: 1d listed, community yes" in lines
    assert "eth CapMrktCurUSD: 1d not listed, community no" in lines
    assert "eth SplyCur: 1d listed, community yes, from 2015-07-30" in lines
    assert f"eth {VOLUME_METRIC}: 1d listed, community yes, from 2016-01-04" in lines
    text = "\n".join(lines)
    assert not [t for t in fixture_tokens() if t in text]
    assert not re.search(r"20(2[3-9]|[3-9]\d)", text)
    assert ("btc", "catalog-2") in {(p["assets"], p.get("next_page_token")) for _, p in fake.calls}


def test_inspect_counts_an_asset_missing_from_the_catalog() -> None:
    def respond(url: str, params: dict[str, str]) -> bytes:
        return page()

    inspection = coinmetrics.inspect(FakeFetcher(respond), sources((BTC,)))
    assert inspection.failures == 1
    assert inspection.lines == ["btc: not in the catalog"]


def test_inspect_joins_an_asset_metrics_listed_across_pages() -> None:
    first: dict[str, object] = {
        "asset": "btc",
        "metrics": [{"metric": "PriceUSD", "frequencies": []}],
    }
    later: dict[str, object] = {
        "asset": "btc",
        "metrics": [{"metric": "SplyCur", "frequencies": [{"frequency": "1d", "community": True}]}],
    }

    def respond(url: str, params: dict[str, str]) -> bytes:
        if params.get("next_page_token") is None:
            return page(first, token="next")
        return page(later)

    inspection = coinmetrics.inspect(FakeFetcher(respond), sources((BTC,)))
    assert "btc SplyCur: 1d listed, community yes" in inspection.lines
