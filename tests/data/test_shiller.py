"""Tests for the Shiller adapter on an `ie_data.xls` fixture (decision 0013)."""

from pathlib import Path

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
from fixtures.build_sources import SHILLER_HEADER, SHILLER_ROWS, shiller_xls
from tiferet.data import shiller
from tiferet.data.adapter import Batch, ingest
from tiferet.data.errors import SourceFormatError
from tiferet.data.source_config import load_sources

PAGE_URL = "https://shillerdata.com/"
XLS_URL = "https://img1.wsimg.com/blobby/go/fixture/downloads/fixture-id/ie_data.xls?ver=1"
FILES = {PAGE_URL: fixture("shiller", "page.html"), XLS_URL: fixture("shiller", "ie_data.xls")}


def fetcher(files: dict[str, bytes] = FILES) -> FakeFetcher:
    return FakeFetcher(lambda url, params: files[url])


def run(tmp_path: Path, files: dict[str, bytes] = FILES) -> Batch:
    batch = shiller.collect(fetcher(files), load_sources(), smoke=False)
    ingest(batch, silver_roots(tmp_path), tmp_path / "bronze", max_row_loss=0.001)
    return batch


def series(batch: Batch, series_id: str) -> pd.DataFrame:
    return batch.silver[batch.silver["series_id"] == series_id]


def test_link_is_found_on_the_page_and_resolved() -> None:
    fake = fetcher()
    shiller.collect(fake, load_sources(), smoke=False)
    assert [url for url, _ in fake.calls] == [PAGE_URL, XLS_URL]


def test_dates_and_values_are_read_from_the_data_sheet() -> None:
    batch = shiller.collect(fetcher(), load_sources(), smoke=False)
    price = series(batch, "shiller_sp_price").set_index("obs_date")["value"]
    assert list(price.index) == list(
        pd.to_datetime(
            ["2021-10-01", "2022-04-01", "2022-07-01", "2022-10-01", "2022-11-01", "2022-12-01"]
        )
    )
    assert price[pd.Timestamp("2021-10-01")] == pytest.approx(4566.5)
    assert len(series(batch, "shiller_sp_earnings")) == 3  # blank cells are dropped
    assert len(series(batch, "shiller_sp_dividend")) == 5
    assert set(batch.silver["vintage_kind"]) == {"approximated"}


def test_publication_rules() -> None:
    batch = shiller.collect(fetcher(), load_sources(), smoke=False)
    # Price: end of the month.
    assert known_at(series(batch, "shiller_sp_price"), "2021-10-01") == [utc("2021-11-01 03:59:59")]
    # Earnings: end of the quarter plus 183 days.
    earnings = series(batch, "shiller_sp_earnings")
    assert known_at(earnings, "2022-04-01") == [utc("2022-12-31 04:59:59")]
    assert known_at(earnings, "2022-07-01") == [utc("2023-04-02 03:59:59")]
    # CPI and the long rate follow CPIAUCSL (45 days) and DGS10 (4 days).
    assert known_at(series(batch, "shiller_cpi"), "2022-10-01") == [utc("2022-12-16 04:59:59")]
    assert known_at(series(batch, "shiller_long_rate"), "2022-11-01") == [
        utc("2022-12-05 04:59:59")
    ]


def test_output_passes_pit_get_and_respects_the_holdout_boundary(tmp_path: Path) -> None:
    run(tmp_path)
    roots = silver_roots(tmp_path)
    assert_partitioned(roots)
    # December 2022 ends at 23:59:59 New York, which is 1 January in Berlin: holdout.
    assert len(research_view(roots, "shiller_sp_price")) == 5
    assert len(production_view(roots, "shiller_sp_price")) == 6
    assert len(research_view(roots, "shiller_sp_earnings")) == 2


def page_with(links: str) -> bytes:
    return f"<html><body>{links}</body></html>".encode()


@pytest.mark.parametrize(
    ("files", "message"),
    [
        (
            {
                **FILES,
                XLS_URL: shiller_xls(
                    tuple("GS10" if h == "Rate GS10" else h for h in SHILLER_HEADER)
                ),
            },
            r"header row lacks \['Rate GS10'\]",
        ),
        ({**FILES, XLS_URL: shiller_xls(("Year", *SHILLER_HEADER[1:]))}, "no header row"),
        ({**FILES, XLS_URL: shiller_xls(rows=())}, "no monthly rows"),
        ({**FILES, XLS_URL: b"not a spreadsheet"}, "not a readable spreadsheet"),
        ({**FILES, PAGE_URL: page_with("<a href='/a.xls'>x</a>")}, "found 0"),
        (
            {
                **FILES,
                PAGE_URL: page_with("<a href='/1/ie_data.xls'>a</a><a href='/2/ie_data.xls'>b</a>"),
            },
            "found 2",
        ),
        ({**FILES, PAGE_URL: page_with("<a href='http://x.org/ie_data.xls'>x</a>")}, "not https"),
    ],
    ids=["renamed-column", "missing-header", "no-rows", "not-xls", "no-link", "two-links", "http"],
)
def test_format_errors_write_nothing(tmp_path: Path, files: dict[str, bytes], message: str) -> None:
    run(tmp_path)
    before = snapshot(tmp_path)
    with pytest.raises(SourceFormatError, match=f"shiller: .*{message}"):
        run(tmp_path, files)
    assert snapshot(tmp_path) == before


def test_month_written_as_one_decimal_is_october() -> None:
    rows = ((2022.1, *SHILLER_ROWS[0][1:]),)
    raw = shiller.parse(shiller_xls(rows=rows), load_sources().shiller)
    assert list(raw["obs_date"]) == [pd.Timestamp("2022-10-01")]


def test_early_months_wait_for_the_end_of_the_following_year() -> None:
    template = SHILLER_ROWS[0]
    rows = (
        (1900.02, *template[1:]),
        (1926.02, *template[1:]),
        (1952.02, *template[1:]),
        (1953.02, *template[1:]),
    )
    config = load_sources()
    wide = shiller.parse(shiller_xls(rows=rows), config.shiller)
    batch = pd.concat(shiller.stamp(wide, config.shiller, config.fred))
    earnings = batch[batch["series_id"] == "shiller_sp_earnings"]
    # Annual Cowles data before 1926: end of 1901 plus 183 days.
    assert known_at(earnings, "1900-02-01") == [utc("1902-07-03 04:59:59")]
    # Quarterly totals from 1926: end of March 1926 plus 183 days.
    assert known_at(earnings, "1926-02-01") == [utc("1926-10-01 04:59:59")]
    rate = batch[batch["series_id"] == "shiller_long_rate"]
    assert known_at(rate, "1952-02-01") == [utc("1954-01-05 04:59:59")]
    assert known_at(rate, "1953-02-01") == [utc("1953-03-05 04:59:59")]
    cpi = batch[batch["series_id"] == "shiller_cpi"]
    assert known_at(cpi, "1900-02-01") == [utc("1902-02-15 04:59:59")]
