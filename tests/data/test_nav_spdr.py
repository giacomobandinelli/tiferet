"""Tests for the SPDR NAV history adapter on a synthetic workbook (decisions 0013, 0016)."""

import io
import zipfile
from pathlib import Path

import pandas as pd
import pytest

from data.adapter_support import (
    FakeFetcher,
    assert_partitioned,
    assert_same_archive,
    fixture,
    known_at,
    production_view,
    research_view,
    silver_roots,
    utc,
)
from fixtures.build_sources import (
    SPDR_HEADER,
    SPDR_ROWS,
    ZIP_CREATE_SYSTEM,
    ZIP_TIME,
    spdr_xlsx,
)
from tiferet.data import nav_spdr
from tiferet.data.adapter import Batch, ingest, smoke_lines
from tiferet.data.cli import main
from tiferet.data.errors import SourceFormatError
from tiferet.data.source_config import Sources, SpdrLayout, load_sources
from tiferet.data.store import HOLDOUT_START

URL = (
    "https://www.ssga.com/de/de/intermediary/library-content/products/fund-data/etfs/emea/"
    "navhist-emea-en-zpde-gy.xlsx"
)
LAYOUT = SpdrLayout(
    sheet="NAV History",
    header=SPDR_HEADER,
    date_column="Date",
    nav_column="NAV",
    date_format="%d-%b-%Y",
)


def sources(layout: SpdrLayout | None = LAYOUT) -> Sources:
    config = load_sources()
    return config.model_copy(update={"spdr": config.spdr.model_copy(update={"layout": layout})})


def fetcher(content: bytes | None = None) -> FakeFetcher:
    files = {URL: content if content is not None else fixture("spdr", "navhist.xlsx")}
    return FakeFetcher(lambda url, params: files[url])


def collect(content: bytes | None = None, layout: SpdrLayout | None = LAYOUT) -> Batch:
    return nav_spdr.collect(fetcher(content), sources(layout), smoke=False)


def test_the_committed_fixture_matches_the_builder() -> None:
    assert_same_archive(fixture("spdr", "navhist.xlsx"), spdr_xlsx())


def test_the_builder_writes_the_same_archive_metadata_on_every_system() -> None:
    with zipfile.ZipFile(io.BytesIO(spdr_xlsx())) as archive:
        infos = archive.infolist()
    assert [i.filename for i in infos] == sorted(i.filename for i in infos)
    assert {i.create_system for i in infos} == {ZIP_CREATE_SYSTEM}
    assert {i.date_time for i in infos} == {ZIP_TIME}


def test_an_unconfigured_layout_stops_with_a_clear_error() -> None:
    assert load_sources().spdr.layout is None
    fake = fetcher()
    with pytest.raises(SourceFormatError, match=r"layout is not configured yet: run .*--inspect"):
        nav_spdr.collect(fake, load_sources(), smoke=True)
    assert fake.calls == []


def test_cli_smoke_fails_until_the_layout_is_configured() -> None:
    with pytest.raises(SourceFormatError, match="not configured yet"):
        main("spdr", nav_spdr.collect, ["--smoke"], fetcher=fetcher)


def test_rows_are_read_below_the_configured_header() -> None:
    batch = collect()
    assert batch.expected == ("spdr_us_energy_nav",)
    nav = batch.silver.set_index("obs_date")["value"]
    assert list(nav.index) == list(
        pd.to_datetime(["2022-04-14", "2022-12-23", "2022-12-29", "2022-12-30"])
    )
    assert nav[pd.Timestamp("2022-12-23")] == pytest.approx(60.95)  # a NAV held as text
    assert nav[pd.Timestamp("2022-12-30")] == pytest.approx(61.84)
    assert set(batch.silver["vintage_kind"]) == {"approximated"}


def test_publication_rule() -> None:
    silver = collect().silver
    assert known_at(silver, "2022-12-29") == [utc("2022-12-30 11:00")]
    assert known_at(silver, "2022-12-30") == [utc("2023-01-02 11:00")]
    assert known_at(silver, "2022-04-14") == [utc("2022-04-19 10:00")]


def test_output_passes_pit_get_and_respects_the_holdout_boundary(tmp_path: Path) -> None:
    roots = silver_roots(tmp_path)
    ingest(collect(), roots, tmp_path / "bronze", max_row_loss=0.001)
    assert_partitioned(roots)
    development = pd.read_parquet(roots.development / "spdr_us_energy_nav.parquet")
    assert (development["known_at"] < HOLDOUT_START).all()
    research = research_view(roots, "spdr_us_energy_nav")
    assert len(research) == 3
    assert pd.Timestamp("2022-12-30") in set(production_view(roots, "spdr_us_energy_nav").obs_date)


def test_smoke_prints_counts_only() -> None:
    lines = smoke_lines(collect())
    assert lines[1] == "spdr_us_energy_nav: 4 rows, 3 development, 1 sealed"


@pytest.mark.parametrize(
    ("header", "rows", "match"),
    [
        (("Day", *SPDR_HEADER[1:]), SPDR_ROWS, "no header row"),
        (SPDR_HEADER, [("30-Dec-2022", "n/a", 1.0, 1.0)], "a NAV cell is not a number"),
        (SPDR_HEADER, [("30-Dec-2022", -1.0, 1.0, 1.0)], "not a positive number"),
        (SPDR_HEADER, [SPDR_ROWS[0], SPDR_ROWS[0]], "a NAV date appears twice"),
        (SPDR_HEADER, [(44925.0, 61.84, 1.0, 1.0)], "the date column holds a number"),
        (
            SPDR_HEADER,
            [SPDR_ROWS[0], ("note", None, None, None), SPDR_ROWS[1]],
            "a dated row follows the end",
        ),
    ],
)
def test_malformed_files_are_refused(
    header: tuple[str, ...], rows: list[tuple[object, ...]], match: str
) -> None:
    with pytest.raises(SourceFormatError, match=match):
        collect(spdr_xlsx(header, rows))  # type: ignore[arg-type]


def test_a_missing_sheet_or_an_unreadable_file_is_refused() -> None:
    with pytest.raises(SourceFormatError, match="no sheet 'Prices'"):
        collect(layout=LAYOUT.model_copy(update={"sheet": "Prices"}))
    with pytest.raises(SourceFormatError, match="not a readable spreadsheet"):
        collect(b"not a workbook")


def test_inspect_prints_structure_and_no_value_or_date() -> None:
    inspection = nav_spdr.inspect(fetcher(), load_sources())
    assert inspection.failures == 0
    text = "\n".join(inspection.lines)
    assert "us_energy: sheets: Notes, NAV History" in text
    assert "row 4: Date | NAV | Shares Outstanding | Total Net Assets" in text
    assert "us_energy: NAV History first data row cell types: text, number, number, number" in text
    for token in ("2022", "Dec", "61.84", "61.27", "60.95", "58.02", "1200000", "74208000"):
        assert token not in text, token


def test_inspect_reports_an_unreadable_file() -> None:
    inspection = nav_spdr.inspect(fetcher(b"<html>"), load_sources())
    assert inspection.failures == 1
    assert "not a readable spreadsheet" in inspection.lines[0]
