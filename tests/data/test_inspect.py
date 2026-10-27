"""Inspect mode prints structure only, never a value or an observation date (decision 0013)."""

import json
import re

import pytest

from data.adapter_support import FIXTURES, FakeFetcher, fixture
from fixtures.build_sources import FRENCH_ZIPS, SHILLER_ROWS
from tiferet.data import ecb, french, shiller
from tiferet.data.cli import main
from tiferet.data.fetch import FetchError
from tiferet.data.source_config import Sources, load_sources

PAGE_URL = "https://shillerdata.com/"
XLS_URL = "https://img1.wsimg.com/blobby/go/fixture/downloads/fixture-id/ie_data.xls?ver=1"


def leaked(tokens: set[str], text: str) -> list[str]:
    """The tokens that appear in `text` as whole numbers or dates."""
    return [t for t in tokens if re.search(rf"(?<![\d.]){re.escape(t)}(?![\d.])", text)]


def french_sources() -> Sources:
    config = load_sources()
    files = tuple(f for f in config.french.files if f.file in FRENCH_ZIPS)
    return config.model_copy(update={"french": config.french.model_copy(update={"files": files})})


def french_tokens() -> set[str]:
    """Every field of every data row in the French fixtures: dates and values."""
    tokens: set[str] = set()
    for path in (FIXTURES / "french").glob("*.csv"):
        for line in path.read_text(encoding="latin-1").splitlines():
            fields = [f.strip() for f in line.split(",")]
            if fields[0].isdigit():
                tokens.update(f for f in fields if f)
    return tokens


def test_french_prints_titles_and_headers_only() -> None:
    zips = {name: fixture("french", name) for name in FRENCH_ZIPS}
    fake = FakeFetcher(lambda url, params: zips[url.rsplit("/", 1)[1]])
    inspection = french.inspect(fake, french_sources())
    assert inspection.failures == 0
    lines = inspection.lines
    text = "\n".join(lines)
    assert "49_Industry_Portfolios_CSV.zip: 4 tables" in lines
    assert "  title: Average Value Weighted Returns -- Monthly" in lines
    assert "  title: Annual Factors: January-December" in lines
    assert "  header: Mkt-RF, SMB, HML, RF" in lines
    assert "  rows: 5, date widths [6]" in lines
    assert leaked(french_tokens(), text) == []


def test_french_reports_a_failed_file_and_continues() -> None:
    def respond(url: str, params: dict[str, str]) -> bytes:
        if "daily" in url:
            raise FetchError("HTTP 404 from https://x: Not Found", status=404, detail="Not Found")
        return fixture("french", url.rsplit("/", 1)[1])

    inspection = french.inspect(FakeFetcher(respond), french_sources())
    assert inspection.failures == 1
    lines = inspection.lines
    assert "F-F_Research_Data_Factors_daily_CSV.zip: HTTP 404 from https://x: Not Found" in lines
    assert "49_Industry_Portfolios_CSV.zip: 4 tables" in lines


def shiller_tokens() -> set[str]:
    tokens = {str(v) for row in SHILLER_ROWS for v in row if v is not None}
    return tokens | {"2021", "2022"}


def test_shiller_prints_sheet_names_and_header_cells_only() -> None:
    files = {PAGE_URL: fixture("shiller", "page.html"), XLS_URL: fixture("shiller", "ie_data.xls")}
    inspection = shiller.inspect(FakeFetcher(lambda url, params: files[url]), load_sources())
    assert inspection.failures == 0
    lines = inspection.lines
    assert lines[0] == f"link: {XLS_URL}"
    assert lines[1] == "sheets: Notes, Data"
    assert "row 7: Date | P | D | E | CPI | Fraction | Rate GS10 | Price | Dividend" in "\n".join(
        lines
    )
    assert leaked(shiller_tokens(), "\n".join(lines)) == []


def test_shiller_reports_a_failed_download() -> None:
    def respond(url: str, params: dict[str, str]) -> bytes:
        if url == XLS_URL:
            raise FetchError(
                "URLError from https://img1: timed out (no redirects; no final status)"
            )
        return fixture("shiller", "page.html")

    inspection = shiller.inspect(FakeFetcher(respond), load_sources())
    assert inspection.failures == 1
    lines = inspection.lines
    assert lines == [
        f"link: {XLS_URL}",
        "shiller: URLError from https://img1: timed out (no redirects; no final status)",
    ]


def test_ecb_prints_dimension_ids_only() -> None:
    urls = {
        "https://data-api.ecb.europa.eu/service/data/EXR/D.USD.EUR.SP00.A": "exr_usd.json",
        "https://data-api.ecb.europa.eu/service/data/EST/B.EU000A2X2A25.WT": "estr.json",
    }

    def respond(url: str, params: dict[str, str]) -> bytes:
        assert params["endPeriod"] == "2022-12-31"
        if url in urls:
            return fixture("ecb", urls[url])
        raise FetchError("HTTP 404 from x: No results found.", status=404)

    inspection = ecb.inspect(FakeFetcher(respond), load_sources())
    assert inspection.failures == len(inspection.lines) - 2
    lines = inspection.lines
    expected = (
        "EXR.D.USD.EUR.SP00.A: series FREQ=D, CURRENCY=USD, CURRENCY_DENOM=EUR, "
        "EXR_TYPE=SP00, EXR_SUFFIX=A; observation TIME_PERIOD"
    )
    assert expected in lines
    tokens: set[str] = set()
    for name in urls.values():
        message = json.loads(fixture("ecb", name))
        for observation in message["dataSets"][0]["series"].values():
            tokens |= {str(v[0]) for v in observation["observations"].values() if v[0] is not None}
        time = message["structure"]["dimensions"]["observation"][0]["values"]
        tokens |= {v["id"] for v in time}
    assert leaked(tokens, "\n".join(lines)) == []


def test_cli_inspect_prints_structure(capsys: pytest.CaptureFixture[str]) -> None:
    zips = {name: fixture("french", name) for name in FRENCH_ZIPS}
    fake = FakeFetcher(lambda url, params: zips[url.rsplit("/", 1)[1]])
    code = main(
        "french",
        french.collect,
        ["--inspect"],
        inspect=french.inspect,
        fetcher=lambda: fake,
        sources=french_sources,
    )
    assert code == 0
    assert "  header: Mkt-RF, SMB, HML, RF" in capsys.readouterr().out


def test_cli_inspect_requires_an_inspect_function() -> None:
    with pytest.raises(SystemExit, match="no inspect mode"):
        main("french", french.collect, ["--inspect"], sources=french_sources)


def test_cli_inspect_exits_with_failure_when_an_item_fails() -> None:
    def respond(url: str, params: dict[str, str]) -> bytes:
        raise FetchError("HTTP 404 from x: Not Found", status=404)

    code = main(
        "french",
        french.collect,
        ["--inspect"],
        inspect=french.inspect,
        fetcher=lambda: FakeFetcher(respond),
        sources=french_sources,
    )
    assert code == 1
