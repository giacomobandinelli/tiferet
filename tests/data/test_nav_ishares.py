"""Tests for the iShares fund data adapter on synthetic fixtures (decisions 0013, 0016)."""

import re
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit

import pandas as pd
import pytest

from data.adapter_support import (
    FakeFetcher,
    assert_partitioned,
    known_at,
    production_view,
    research_view,
    silver_roots,
    text_fixture,
    utc,
)
from tiferet.config import load_holdings
from tiferet.data import nav_ishares
from tiferet.data.adapter import Batch, ingest, smoke_lines
from tiferet.data.cli import main
from tiferet.data.errors import SourceFormatError
from tiferet.data.source_config import Sources, load_sources
from tiferet.data.spreadsheetml import Cell, read_workbook
from tiferet.data.store import HOLDOUT_START

US_PAGE = "https://www.ishares.com/us/products/239710/ishares-russell-2000-etf"
US_PORTFOLIO = "239710"
UCITS_XML = text_fixture("ishares", "fund_ucits.xml")
US_XML = text_fixture("ishares", "fund_us.xml")
UCITS_NAVS = ("412,15", "410,02", "405.77", "430,10", "399,64", "1.012,40", "250,08")
US_VALUES = ("174.38", "175.06", "178.12", "180.11", "165.32", "198.54", "0.62", "0.47")
DATES = ("Dez.2022", "Apr.2022", "Okt.2021", "März 2018", "Juli2017", ", 2022", "2022-09-26")


def portfolio_id(page_url: str) -> str:
    return page_url.rstrip("/").rsplit("/", 1)[-1]


def currencies() -> dict[str, str]:
    """The configured currency of each UCITS fund, by portfolio id."""
    holdings, config = load_holdings(), load_sources()
    return {
        portfolio_id(nav_ishares.ucits_page(f, holdings, config.ishares)): f.currency
        for f in config.ishares.ucits.funds
    }


def responder(
    ucits_xml: str = UCITS_XML,
    us_xml: str = US_XML,
    page: str | None = None,
) -> FakeFetcher:
    currency = currencies()

    def respond(url: str, params: dict[str, str]) -> bytes:
        if "get-fund-document" in url:
            pid = dict(parse_qsl(urlsplit(url).query))["portfolioId"]
            if pid == US_PORTFOLIO:
                return us_xml.encode("utf-8")
            return ucits_xml.replace("CURRENCY", currency[pid]).encode("utf-8")
        if url == US_PAGE:
            return text_fixture("ishares", "page_us.html").encode("utf-8")
        text = page or text_fixture("ishares", "page_ucits.html")
        return text.replace("PORTFOLIO", portfolio_id(url)).encode("utf-8")

    return FakeFetcher(respond)


def collect(fetcher: FakeFetcher | None = None) -> Batch:
    return nav_ishares.collect(fetcher or responder(), load_sources(), smoke=False)


def series(batch: Batch, series_id: str) -> pd.DataFrame:
    return batch.silver[batch.silver["series_id"] == series_id].set_index("obs_date")


def test_every_configured_series_is_produced() -> None:
    batch = collect()
    assert set(batch.silver["series_id"]) == set(batch.expected)
    assert len(batch.expected) == 9
    assert "ishares_sp500_nav" in batch.expected
    assert {"ishares_us_iwm_nav", "ishares_us_iwm_dist"} <= set(batch.expected)
    assert set(batch.silver["vintage_kind"]) == {"approximated"}


def test_each_page_is_read_before_its_file() -> None:
    fake = responder()
    collect(fake)
    urls = [url for url, _ in fake.calls]
    assert urls[0] == "https://www.ishares.com/de/privatanleger/de/produkte/253741/"
    assert urls[1].startswith(
        "https://www.blackrock.com/varnish-api/product-data/api/v1/get-fund-document?"
    )
    assert dict(parse_qsl(urlsplit(urls[1]).query))["portfolioId"] == "253741"
    assert urls[-2:][0] == US_PAGE


def test_german_dates_and_decimal_commas() -> None:
    nav = series(collect(), "ishares_sp500_nav")["value"]
    assert list(nav.index) == list(
        pd.to_datetime(
            [
                "2017-07-18",
                "2018-03-01",
                "2021-10-26",
                "2022-04-14",
                "2022-12-23",
                "2022-12-29",
                "2022-12-30",
            ]
        )
    )
    assert nav[pd.Timestamp("2017-07-18")] == pytest.approx(250.08)
    assert nav[pd.Timestamp("2018-03-01")] == pytest.approx(1012.40)
    assert nav[pd.Timestamp("2022-12-23")] == pytest.approx(405.77)
    assert nav[pd.Timestamp("2022-12-30")] == pytest.approx(412.15)


def test_us_nav_and_distributions() -> None:
    batch = collect()
    nav = series(batch, "ishares_us_iwm_nav")["value"]
    assert len(nav) == 6
    assert nav[pd.Timestamp("2022-12-23")] == pytest.approx(178.12)
    assert nav[pd.Timestamp("2022-09-26")] == pytest.approx(165.32)
    dist = series(batch, "ishares_us_iwm_dist")["value"]
    # "--", an empty cell and a zero amount are not stored.
    assert dist.to_dict() == {
        pd.Timestamp("2022-09-26"): pytest.approx(0.47),
        pd.Timestamp("2022-12-12"): pytest.approx(0.62),
    }


def test_publication_rules() -> None:
    batch = collect()
    ucits = batch.silver[batch.silver["series_id"] == "ishares_sp500_nav"]
    # Noon in Berlin on the next TARGET business day.
    assert known_at(ucits, "2022-12-29") == [utc("2022-12-30 11:00")]
    assert known_at(ucits, "2022-12-30") == [utc("2023-01-02 11:00")]
    # Good Friday and Easter Monday are TARGET holidays; summer time in Berlin.
    assert known_at(ucits, "2022-04-14") == [utc("2022-04-19 10:00")]
    us = batch.silver[batch.silver["series_id"] == "ishares_us_iwm_nav"]
    # 09:00 in New York on the next US business day.
    assert known_at(us, "2022-12-29") == [utc("2022-12-30 14:00")]
    assert known_at(us, "2022-12-23") == [utc("2022-12-27 14:00")]  # Christmas observed
    assert known_at(us, "2022-12-30") == [utc("2023-01-03 14:00")]  # New Year observed
    assert known_at(us, "2022-04-14") == [utc("2022-04-18 13:00")]  # Good Friday
    dist = batch.silver[batch.silver["series_id"] == "ishares_us_iwm_dist"]
    assert known_at(dist, "2022-12-12") == [utc("2022-12-13 14:00")]


def test_output_passes_pit_get_and_respects_the_holdout_boundary(tmp_path: Path) -> None:
    batch = collect()
    roots = silver_roots(tmp_path)
    ingest(batch, roots, tmp_path / "bronze", max_row_loss=0.001)
    assert_partitioned(roots)
    for series_id in batch.expected:
        development = pd.read_parquet(roots.development / f"{series_id}.parquet")
        assert (development["known_at"] < HOLDOUT_START).all()
        research = research_view(roots, series_id)
        assert not research.empty
        assert (research["known_at"] < HOLDOUT_START).all()
    sealed = pd.read_parquet(roots.holdout / "ishares_sp500_nav.parquet")
    assert list(sealed["obs_date"]) == [pd.Timestamp("2022-12-30")]
    assert pd.Timestamp("2022-12-30") not in set(research_view(roots, "ishares_sp500_nav").obs_date)
    assert pd.Timestamp("2022-12-30") in set(production_view(roots, "ishares_us_iwm_nav").obs_date)
    assert not (roots.holdout / "ishares_us_iwm_dist.parquet").exists()


def test_smoke_prints_counts_only() -> None:
    lines = smoke_lines(collect())
    assert lines[0].startswith("ishares: 16 files, ")
    assert "ishares_sp500_nav: 7 rows, 6 development, 1 sealed" in lines
    assert "ishares_us_iwm_dist: 2 rows, 2 development, 0 sealed" in lines
    text = "\n".join(lines)
    assert not any(value in text for value in (*UCITS_NAVS, *US_VALUES))


def test_cli_smoke(capsys: pytest.CaptureFixture[str]) -> None:
    assert main("ishares", nav_ishares.collect, ["--smoke"], fetcher=responder) == 0
    assert "ishares_us_iwm_nav: 6 rows, 5 development, 1 sealed" in capsys.readouterr().out


# Strict parsing -----------------------------------------------------------------------------


FUND_ID = re.compile(r"^ishares: ishares_(?:us_)?[a-z0-9_]+_nav: ")
"""The start of every format error of a fund: the source, then the fund's series id."""


def refused(match: str, **files: str) -> None:
    with pytest.raises(SourceFormatError, match=match) as caught:
        collect(responder(**files))
    assert FUND_ID.match(str(caught.value)), caught.value


def test_a_currency_other_than_the_configured_one_is_refused() -> None:
    xml = UCITS_XML.replace(
        'Währung der Reihe</ss:Data></ss:Cell><ss:Cell><ss:Data ss:Type="String">CURRENCY',
        'Währung der Reihe</ss:Data></ss:Cell><ss:Cell><ss:Data ss:Type="String">GBP',
    )
    assert xml != UCITS_XML
    refused("series currency is 'GBP'", ucits_xml=xml)


def test_a_missing_currency_row_is_refused() -> None:
    refused("one 'Währung der Reihe' row", ucits_xml=UCITS_XML.replace("Währung der Reihe", "X"))


@pytest.mark.parametrize(
    ("old", "new", "match"),
    [
        ("26.Okt.2021", "26.Foo.2021", "unknown month name 'Foo'"),
        ("26.Okt.2021", "31.Nov.2021", "a date does not exist"),
        ("399,64", "399.64", "a NAV cell is not a number"),
        ("399,64", "n/a", "a NAV cell is not a number"),
        ("399,64", "0,00", "not a positive number"),
        ("Historische NAVs", "NAVs", "does not start with 'Historische NAVs'"),
        ("Historical NAVs", "NAVs", "no sheet 'Historical NAVs'"),
        ("29.Dez.2022", "30.Dez.2022", "a NAV date appears twice"),
    ],
)
def test_malformed_ucits_files_are_refused(old: str, new: str, match: str) -> None:
    assert old in UCITS_XML
    refused(match, ucits_xml=UCITS_XML.replace(old, new, 1))


def test_a_row_with_an_extra_cell_is_refused() -> None:
    old = '<ss:Data ss:Type="String">250,08</ss:Data></ss:Cell>'
    xml = UCITS_XML.replace(old, old + '<ss:Cell><ss:Data ss:Type="String">x</ss:Data></ss:Cell>')
    refused("does not hold a date and a NAV", ucits_xml=xml)


def test_a_dated_row_after_the_closing_text_is_refused() -> None:
    row = (
        '<ss:Row><ss:Cell><ss:Data ss:Type="String">02.Jan.2017</ss:Data></ss:Cell>'
        '<ss:Cell><ss:Data ss:Type="String">1,00</ss:Data></ss:Cell></ss:Row>\n'
    )
    xml = UCITS_XML.replace(
        '</ss:Table>\n</ss:Worksheet>\n<ss:Worksheet ss:Name="Holdings">',
        row + '</ss:Table>\n</ss:Worksheet>\n<ss:Worksheet ss:Name="Holdings">',
    )
    assert xml != UCITS_XML
    refused("a dated row follows the end of the NAV rows", ucits_xml=xml)


@pytest.mark.parametrize(
    ("old", "new", "match"),
    [
        ("NAV per Share", "NAV", "has no header row"),
        ("174.38</ss:Data>", "174,38</ss:Data>", "a number cell is malformed"),
        ('"String">178.12', '"String">178,12', "a NAV is not a number"),
        ('"Number">0.62', '"Number">-0.62', "an ex-dividend amount is negative"),
        ('"String">0.47', '"String">n/a', "an ex-dividend amount is not a number"),
        ("2022-09-26T00:00:00.000", "2022-09-26T15:00:00.000", "holds a time of day"),
        ("Dec 29, 2022", "Dec 30, 2022", "a NAV date appears twice"),
        ("Past performance does not", "Dec 01, 2021 does not", None),
    ],
)
def test_malformed_us_files_are_refused(old: str, new: str, match: str | None) -> None:
    assert old in US_XML
    xml = US_XML.replace(old, new, 1)
    if match is None:
        # A note that merely starts with a date-like word is not a date in the configured format.
        collect(responder(us_xml=xml))
        return
    refused(match, us_xml=xml)


def test_a_dated_us_row_after_the_end_of_the_rows_is_refused() -> None:
    xml = US_XML.replace("Past performance does not guarantee future results.", "Dec 01, 2021")
    refused("a dated row follows the end of the NAV rows", us_xml=xml)


# Links --------------------------------------------------------------------------------------


DATA_URL = "https://www.blackrock.com/varnish-api/product-data/api/v1/get-fund-document"
LINK = (
    f'<a class="icon-xls-export" href="{DATA_URL}?appType=fixture'
    "&amp;appSubType=fixture&amp;targetSite=de&amp;locale=de_DE&amp;portfolioId=PORTFOLIO"
    '&amp;component=fundData&amp;userType=individual">Daten herunterladen</a>'
)
PAGE = text_fixture("ishares", "page_ucits.html")


@pytest.mark.parametrize(
    ("page", "match"),
    [
        (PAGE.replace(LINK, ""), "expected one fund data link .* found 0"),
        (PAGE.replace(LINK, LINK + LINK.replace("fundData", "holdings")), "found 2"),
        (
            PAGE.replace(DATA_URL, DATA_URL.replace("https:", "http:")),
            "not on https://www.blackrock",
        ),
        (PAGE.replace("https://www.blackrock.com/varnish-api", "/varnish-api"), "not on https"),
        (
            PAGE.replace(DATA_URL, DATA_URL.replace("www.blackrock.com", "www.ishares.com")),
            "not on",
        ),
        (
            PAGE.replace(DATA_URL, DATA_URL.replace("www.blackrock.com", "files.example.org")),
            "not on",
        ),
        (
            PAGE.replace(
                DATA_URL, DATA_URL.replace("www.blackrock.com", "www.blackrock.com.evil.org")
            ),
            "not on",
        ),
        (
            PAGE.replace(DATA_URL, DATA_URL.replace("www.blackrock.com", "www.blackrock.com:8443")),
            "not on",
        ),
        (PAGE.replace(DATA_URL, DATA_URL.replace("https://", "https://user@")), "not on"),
        (PAGE.replace("&amp;userType=individual", ""), r"lacks \['userType'\]"),
    ],
)
def test_the_fund_data_link_is_checked(page: str, match: str) -> None:
    assert page != PAGE
    with pytest.raises(SourceFormatError, match=match) as caught:
        collect(responder(page=page))
    assert str(caught.value).startswith("ishares: ishares_nasdaq100_nav: "), caught.value


def test_the_same_link_twice_counts_once() -> None:
    collect(responder(page=PAGE.replace(LINK, LINK + LINK)))


def test_every_ucits_holding_page_is_an_ishares_product_page() -> None:
    holdings = load_holdings()
    config = load_sources().ishares
    pages = [nav_ishares.ucits_page(f, holdings, config) for f in config.ucits.funds]
    assert all(p.startswith("https://www.ishares.com/de/privatanleger/de/produkte/") for p in pages)
    ishares = {h.key for h in holdings.holdings if h.name.value.startswith("iShares")}
    assert {f.holding for f in load_sources().ishares.ucits.funds} == ishares


def test_a_holding_without_an_ishares_page_is_refused() -> None:
    config = load_sources().ishares
    fund = config.ucits.funds[0].model_copy(update={"holding": "us_energy"})
    with pytest.raises(ValueError, match=r"not on www\.ishares\.com"):
        nav_ishares.ucits_page(fund, load_holdings(), config)
    with pytest.raises(ValueError, match="not a holding"):
        nav_ishares.ucits_page(fund.model_copy(update={"holding": "nope"}), load_holdings(), config)


def test_the_hosts_come_from_the_configuration() -> None:
    config = load_sources()
    assert (config.ishares.page_host, config.ishares.data_host) == (
        "www.ishares.com",
        "www.blackrock.com",
    )
    moved = config.ishares.model_copy(update={"data_host": "files.example.org"})
    sources = config.model_copy(update={"ishares": moved})
    with pytest.raises(SourceFormatError, match=r"_nav: .* not on https://files\.example\.org"):
        nav_ishares.collect(responder(), sources, smoke=False)


# Inspect ------------------------------------------------------------------------------------


def test_inspect_prints_structure_and_no_value_or_date() -> None:
    inspection = nav_ishares.inspect(responder(), load_sources())
    assert inspection.failures == 0
    text = "\n".join(inspection.lines)
    assert "ishares_sp500_nav: sheets: Fund Header, Key Facts, Historical NAVs, Holdings" in text
    assert "ishares_sp500_nav: Historical NAVs header rows:\n  row 0: Historische NAVs" in text
    assert "ishares_sp500_nav: Historical NAVs first data row cell types: String, String" in text
    assert "ishares_sp500_nav: Währung der Reihe: USD" in text
    assert "ishares_sp500_nav: bare ampersands escaped: 2" in text
    assert "ishares_us_iwm_nav: bare ampersands escaped: 2" in text
    assert (
        "ishares_us_iwm_nav: link https://www.blackrock.com/varnish-api/product-data/api/v1/get-fund-document"
        " with parameters appType, appSubType, targetSite, locale, portfolioId, component, userType"
    ) in text
    assert "row 0: As Of | NAV per Share | Ex-Dividends | Shares Outstanding" in text
    assert (
        "ishares_us_iwm_nav: Historical first data row cell types: String, Number, String, Number"
        in text
    )
    for token in (*UCITS_NAVS, *US_VALUES, *DATES, "2022", "2021", "2017"):
        assert token not in text, token


def test_inspect_reports_a_failed_fund_and_continues() -> None:
    def broken() -> FakeFetcher:
        good = responder()

        def respond(url: str, params: dict[str, str]) -> bytes:
            if url == US_PAGE:
                return b"<html></html>"
            return good.get(url, params).content

        return FakeFetcher(respond)

    inspection = nav_ishares.inspect(broken(), load_sources())
    assert inspection.failures == 1
    assert inspection.lines[-1].startswith(
        "ishares: ishares_us_iwm_nav: expected one fund data link"
    )


def without_distributions() -> Sources:
    config = load_sources()
    us = config.ishares.us
    funds = tuple(f.model_copy(update={"distributions": False}) for f in us.funds)
    ishares = config.ishares.model_copy(update={"us": us.model_copy(update={"funds": funds})})
    return config.model_copy(update={"ishares": ishares})


def test_a_fund_without_distributions_has_no_distributions_series() -> None:
    config = without_distributions()
    xml = US_XML.replace('"Number">0.62', '"Number">0').replace('"String">0.47', '"String">--')
    batch = nav_ishares.collect(responder(us_xml=xml), config, smoke=False)
    assert "ishares_us_iwm_dist" not in batch.expected
    assert "ishares_us_iwm_dist" not in config.series_ids()
    assert set(batch.silver["series_id"]) == set(batch.expected)


def test_an_amount_for_a_fund_without_distributions_is_refused() -> None:
    with pytest.raises(SourceFormatError, match="configured without them"):
        nav_ishares.collect(responder(), without_distributions(), smoke=False)


def test_the_data_host_is_compared_without_regard_to_case() -> None:
    page = PAGE.replace(DATA_URL, DATA_URL.replace("www.blackrock.com", "WWW.BlackRock.com"))
    assert page != PAGE
    collect(responder(page=page))


# Bare ampersands ----------------------------------------------------------------------------


def test_bare_ampersands_in_the_fund_files_are_read_as_written() -> None:
    batch = collect()
    assert set(batch.silver["series_id"]) == set(batch.expected)
    ucits = read_workbook(UCITS_XML.encode("utf-8"), "ishares", "fund").sheets
    assert ucits["Fund Header"][0] == [Cell("String", "Synthetic Core S&P 500 fixture fund")]
    assert [
        Cell("String", "Rating"),
        Cell("String", "Die Bewertung erfolgt durch S&P, Moody's und Fitch."),
    ] in ucits["Key Facts"]
    assert ucits["Holdings"][1][1] == Cell("String", "Fixture & holding\u2019s note\u2019")
    us = read_workbook(US_XML.encode("utf-8"), "ishares", "fund").sheets
    assert us["Disclaimers"][1] == [
        Cell("String", "https://www.example.org/literature#!type=ishares&style=All&view=list")
    ]


@pytest.mark.parametrize("kind", ["ucits", "us"])
def test_a_file_that_is_not_xml_names_the_fund_and_the_position(kind: str) -> None:
    series_id = "ishares_nasdaq100_nav" if kind == "ucits" else "ishares_us_iwm_nav"
    xml = (UCITS_XML if kind == "ucits" else US_XML).replace("</ss:Row>", "</ss:Cell>", 1)
    pattern = (
        rf"^ishares: {series_id}: the fund data file is not XML "
        r"\(mismatched tag: line \d+, column \d+\)$"
    )
    with pytest.raises(SourceFormatError, match=pattern):
        collect(responder(**{f"{kind}_xml": xml}))
