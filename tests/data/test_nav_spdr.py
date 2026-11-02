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
    SPDR_FOOTER,
    SPDR_HEADER,
    SPDR_LABELS,
    SPDR_ROWS,
    ZIP_CREATE_SYSTEM,
    ZIP_TIME,
    spdr_xlsx,
)
from tiferet.data import nav_spdr
from tiferet.data.adapter import Batch, ingest, smoke_lines
from tiferet.data.cli import main
from tiferet.data.errors import SourceFormatError
from tiferet.data.source_config import load_sources
from tiferet.data.store import HOLDOUT_START

URL = (
    "https://www.ssga.com/de/de/intermediary/library-content/products/fund-data/etfs/emea/"
    "navhist-emea-en-zpde-gy.xlsx"
)


def fetcher(content: bytes | None = None) -> FakeFetcher:
    files = {URL: content if content is not None else fixture("spdr", "navhist.xlsx")}
    return FakeFetcher(lambda url, params: files[url])


def collect(content: bytes | None = None) -> Batch:
    return nav_spdr.collect(fetcher(content), load_sources(), smoke=False)


def labels(**changes: str | float | None) -> tuple[tuple[str, str | float | None], ...]:
    """The fixture's label rows with the value of a label replaced, or the label removed
    when the new value is None."""
    return tuple(
        (key, changes.get(key, value))
        for key, value in SPDR_LABELS
        if changes.get(key, value) is not None
    )


def test_the_committed_fixture_matches_the_builder() -> None:
    assert_same_archive(fixture("spdr", "navhist.xlsx"), spdr_xlsx())


def test_the_builder_writes_the_same_archive_metadata_on_every_system() -> None:
    with zipfile.ZipFile(io.BytesIO(spdr_xlsx())) as archive:
        infos = archive.infolist()
    assert [i.filename for i in infos] == sorted(i.filename for i in infos)
    assert {i.create_system for i in infos} == {ZIP_CREATE_SYSTEM}
    assert {i.date_time for i in infos} == {ZIP_TIME}


def test_the_layout_is_configured() -> None:
    layout = load_sources().spdr.layout
    assert layout.sheet == "navhist"
    assert layout.labels == ("Fund Name:", "ISIN:", "Report Date:", "NAV Currency:")
    assert layout.header == SPDR_HEADER


def test_cli_smoke_works(capsys: pytest.CaptureFixture[str]) -> None:
    assert main("spdr", nav_spdr.collect, ["--smoke"], fetcher=fetcher) == 0
    assert "spdr_us_energy_nav: 5 rows, 4 development, 1 sealed" in capsys.readouterr().out


def test_only_the_nav_series_is_stored() -> None:
    batch = collect()
    assert batch.expected == ("spdr_us_energy_nav",)
    assert set(batch.silver["series_id"]) == {"spdr_us_energy_nav"}
    nav = batch.silver.set_index("obs_date")["value"]
    assert list(nav.index) == list(
        pd.to_datetime(["2015-07-07", "2022-04-14", "2022-12-23", "2022-12-29", "2022-12-30"])
    )
    assert nav[pd.Timestamp("2022-12-23")] == pytest.approx(60.95)
    assert nav[pd.Timestamp("2022-12-30")] == pytest.approx(61.84)
    assert nav[pd.Timestamp("2015-07-07")] == pytest.approx(50.0)
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
    assert len(research) == 4
    assert pd.Timestamp("2022-12-30") not in set(research.obs_date)
    assert pd.Timestamp("2022-12-30") in set(production_view(roots, "spdr_us_energy_nav").obs_date)


def test_smoke_prints_counts_only() -> None:
    lines = smoke_lines(collect())
    assert lines[1] == "spdr_us_energy_nav: 5 rows, 4 development, 1 sealed"
    assert not any(value in "\n".join(lines) for value in ("61.84", "60.95", "58.02"))


# Strict parsing -----------------------------------------------------------------------------


def refused(match: str, **parts: object) -> None:
    with pytest.raises(SourceFormatError, match=match):
        collect(spdr_xlsx(**parts))  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("parts", "match"),
    [
        ({"labels": labels(**{"ISIN:": "IE00B3RBWM25"})}, "the ISIN is 'IE00B3RBWM25'"),
        ({"labels": labels(**{"NAV Currency:": "EUR"})}, "the NAV currency is 'EUR'"),
        ({"labels": labels(**{"ISIN:": None})}, "lacks the labels"),
        ({"labels": (*SPDR_LABELS, ("Extra:", "x"))}, "unknown label 'Extra:'"),
        ({"labels": (*SPDR_LABELS, SPDR_LABELS[1])}, "appears twice"),
        ({"labels": (*SPDR_LABELS[:3], ("NAV Currency:", ""))}, "has no single value"),
        ({"header": ("Day", *SPDR_HEADER[1:])}, "unknown label 'Day'"),
        ({"header": ("Date", "Price", *SPDR_HEADER[2:])}, "the header row changed"),
        ({"header": SPDR_HEADER[:3]}, "the header row changed"),
        ({"rows": [("30-12-2022", 61.84, 1.0, 1.0)]}, "does not match dd-Mon-yyyy"),
        ({"rows": [("30-Dez-2022", 61.84, 1.0, 1.0)]}, "unknown month abbreviation 'Dez'"),
        ({"rows": [("31-Nov-2022", 61.84, 1.0, 1.0)]}, "a date does not exist"),
        ({"rows": [(44925.0, 61.84, 1.0, 1.0)]}, "the date column is not text"),
        ({"rows": [("30-Dec-2022", "61.84", 1.0, 1.0)]}, "column 'NAV' is not a number"),
        ({"rows": [("30-Dec-2022", None, 1.0, 1.0)]}, "column 'NAV' is not a number"),
        ({"rows": [("30-Dec-2022", 61.84, "n/a", 1.0)]}, "column 'Shares Outstanding'"),
        ({"rows": [("30-Dec-2022", -1.0, 1.0, 1.0)]}, "not a positive number"),
        ({"rows": [SPDR_ROWS[0], SPDR_ROWS[0]]}, "a NAV date appears twice"),
        ({"rows": [("30-Dec-2022", 61.84, 1.0, 1.0, 9.0)]}, "beyond the header"),
        ({"rows": []}, "no NAV rows"),
        ({"footer": None}, "no footer row"),
        ({"footer": ("Disclaimer text",)}, "does not match dd-Mon-yyyy"),
        ({"after_footer": [("30-Dec-2021", 1.0, 1.0, 1.0)]}, "a row follows the footer"),
        ({"after_footer": [("Another note",)]}, "a row follows the footer"),
    ],
)
def test_malformed_files_are_refused(parts: dict[str, object], match: str) -> None:
    refused(match, **parts)


def test_blank_shares_and_assets_are_accepted() -> None:
    batch = collect(spdr_xlsx(rows=[("30-Dec-2022", 61.84, None, None), SPDR_ROWS[1]]))
    assert len(batch.silver) == 2


def test_the_footer_text_is_read_from_the_configuration() -> None:
    config = load_sources()
    layout = config.spdr.layout.model_copy(update={"footer_prefix": "Other text"})
    sources = config.model_copy(update={"spdr": config.spdr.model_copy(update={"layout": layout})})
    with pytest.raises(SourceFormatError, match="does not match dd-Mon-yyyy"):
        nav_spdr.collect(fetcher(), sources, smoke=False)


def test_a_second_sheet_or_a_renamed_sheet_is_refused() -> None:
    from fixtures.build_sources import SPDR_SHEET, xlsx

    sheet = [*SPDR_LABELS, (), SPDR_HEADER, SPDR_ROWS[0], (), SPDR_FOOTER]
    with pytest.raises(SourceFormatError, match="expected the one sheet 'navhist'"):
        collect(xlsx({SPDR_SHEET: sheet, "extra": [("x",)]}))
    with pytest.raises(SourceFormatError, match="expected the one sheet 'navhist'"):
        collect(xlsx({"prices": sheet}))


def test_an_unreadable_file_is_refused() -> None:
    with pytest.raises(SourceFormatError, match="not a readable spreadsheet"):
        collect(b"not a workbook")


def test_a_holding_without_an_isin_is_refused() -> None:
    from tiferet.config import load_holdings

    holdings = load_holdings()
    stripped = holdings.model_copy(
        update={
            "holdings": tuple(
                h.model_copy(update={"isin": h.isin.model_copy(update={"value": None})})
                if h.key == "us_energy"
                else h
                for h in holdings.holdings
            )
        }
    )
    with pytest.raises(ValueError, match="has no ISIN"):
        nav_spdr.collect(fetcher(), load_sources(), smoke=False, holdings=stripped)


# Inspect ------------------------------------------------------------------------------------


def test_inspect_prints_structure_and_no_value_or_date() -> None:
    inspection = nav_spdr.inspect(fetcher(), load_sources())
    assert inspection.failures == 0
    text = "\n".join(inspection.lines)
    assert "us_energy: sheets: navhist" in text
    assert "row 0: Fund Name: | Synthetic Energy Select Sector Fixture" in text
    assert "row 3: NAV Currency: | USD" in text
    assert "row 5: Date | NAV | Shares Outstanding | Total Net Assets" in text
    assert "us_energy: navhist first data row cell types: text, number, number, number" in text
    for token in ("IE00BWBXM492", "2022", "2015", "Dec", "61.84", "60.95", "58.02", "1200000"):
        assert token not in text, token


def test_inspect_reports_an_unreadable_file() -> None:
    inspection = nav_spdr.inspect(fetcher(b"<html>"), load_sources())
    assert inspection.failures == 1
    assert "not a readable spreadsheet" in inspection.lines[0]


def test_the_report_date_may_be_a_number_or_a_date_cell() -> None:
    changed = (*SPDR_LABELS[:2], ("Report Date:", 44925.0), SPDR_LABELS[3])
    assert len(collect(spdr_xlsx(labels=changed)).silver) == 5


def test_a_number_in_the_isin_or_currency_value_is_refused() -> None:
    changed = (SPDR_LABELS[0], ("ISIN:", 5.0), *SPDR_LABELS[2:])
    refused("the ISIN is 5,", labels=changed)


def test_a_row_above_the_header_without_a_text_label_is_refused() -> None:
    refused("has no text label", labels=((44925.0, "x"), *SPDR_LABELS))
