"""Tests for the Kenneth French Data Library adapter on zipped CSV fixtures (decision 0013)."""

from pathlib import Path

import pandas as pd
import pytest

from data.adapter_support import (
    FIXTURES,
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
from fixtures.build_sources import FRENCH_ZIPS, zip_csv
from tiferet.data import french
from tiferet.data.adapter import Batch, ingest
from tiferet.data.errors import DataLossError, SourceFormatError
from tiferet.data.source_config import Sources, load_sources

ZIPS = {name: fixture("french", name) for name in FRENCH_ZIPS}


def sources() -> Sources:
    config = load_sources()
    files = tuple(f for f in config.french.files if f.file in ZIPS)
    return config.model_copy(update={"french": config.french.model_copy(update={"files": files})})


def fetcher(zips: dict[str, bytes] = ZIPS) -> FakeFetcher:
    return FakeFetcher(lambda url, params: zips[url.rsplit("/", 1)[1]])


def run(tmp_path: Path, zips: dict[str, bytes] = ZIPS) -> Batch:
    batch = french.collect(fetcher(zips), sources(), smoke=False)
    ingest(batch, silver_roots(tmp_path), tmp_path / "bronze", max_row_loss=0.001)
    return batch


def variant(archive: str, old: str, new: str) -> bytes:
    """The archive's CSV with `old` replaced by `new`, zipped again."""
    source, member = FRENCH_ZIPS[archive]
    text = (FIXTURES / "french" / source).read_text(encoding="latin-1")
    assert old in text
    return zip_csv(text.replace(old, new), member)


def series(silver: pd.DataFrame, series_id: str) -> pd.DataFrame:
    return silver[silver["series_id"] == series_id]


def test_factors_are_decimal_returns_with_derived_market() -> None:
    silver = french.collect(fetcher(), sources(), smoke=False).silver
    assert len(series(silver, "kf_us3_mkt_rf_m")) == 5  # the annual section is ignored
    assert len(series(silver, "kf_us3_rf_m")) == 4  # -99.99 is missing
    market = series(silver, "kf_us3_mkt_m").set_index("obs_date")["value"]
    assert len(market) == 4
    assert market[pd.Timestamp("2022-07-01")] == pytest.approx(0.0965)
    assert set(silver["vintage_kind"]) == {"approximated"}


def test_known_ninety_days_after_the_end_of_the_month() -> None:
    silver = french.collect(fetcher(), sources(), smoke=False).silver
    monthly = series(silver, "kf_us3_smb_m")
    assert known_at(monthly, "2022-07-01") == [utc("2022-10-30 03:59:59")]
    assert known_at(monthly, "2022-09-01") == [utc("2022-12-30 04:59:59")]
    daily = series(silver, "kf_us3_smb_d")
    assert known_at(daily, "2022-09-29") == [utc("2022-12-30 04:59:59")]
    assert known_at(daily, "2022-10-03") == [utc("2023-01-30 04:59:59")]


def test_industry_section_is_selected_by_title_and_frequency() -> None:
    silver = french.collect(fetcher(), sources(), smoke=False).silver
    agric = series(silver, "kf_ind49_agric_m").set_index("obs_date")["value"]
    assert list(agric.index) == list(
        pd.to_datetime(["2022-06-01", "2022-07-01", "2022-08-01", "2022-09-01"])
    )
    assert agric.iloc[0] == pytest.approx(-0.025)
    assert len(series(silver, "kf_ind49_soda_m")) == 3  # -999 is missing


def test_output_passes_pit_get_and_respects_the_holdout_boundary(tmp_path: Path) -> None:
    run(tmp_path)
    roots = silver_roots(tmp_path)
    assert_partitioned(roots)
    assert len(research_view(roots, "kf_us3_hml_m")) == 3
    assert len(production_view(roots, "kf_us3_hml_m")) == 5
    assert len(research_view(roots, "kf_us3_hml_d")) == 2
    assert len(research_view(roots, "kf_ind49_other_m")) == 4


@pytest.mark.parametrize(
    ("archive", "old", "new", "message"),
    [
        ("F-F_Research_Data_Factors_CSV.zip", ",Mkt-RF,SMB", ",MktRF,SMB", "lacks columns"),
        (
            "49_Industry_Portfolios_CSV.zip",
            "Average Value Weighted Returns -- Monthly",
            "Average Value Weighted Returns -- Quarterly",
            "0 sections titled",
        ),
        (
            "49_Industry_Portfolios_CSV.zip",
            "Average Value Weighted Returns -- Annual",
            "Average Value Weighted Returns -- Monthly",
            "2 sections titled",
        ),
        ("F-F_Research_Data_Factors_daily_CSV.zip", "20220930,", "2022930,", "not daily"),
        ("F-F_Research_Data_Factors_CSV.zip", "    2.81,", "    2.81,    1.00,", "width differs"),
    ],
    ids=["renamed-column", "missing-section", "duplicate-section", "date-width", "row"],
)
def test_format_errors_write_nothing(
    tmp_path: Path, archive: str, old: str, new: str, message: str
) -> None:
    run(tmp_path)
    before = snapshot(tmp_path)
    with pytest.raises(SourceFormatError, match=f"french: .*{message}"):
        run(tmp_path, {**ZIPS, archive: variant(archive, old, new)})
    assert snapshot(tmp_path) == before


def test_archive_must_hold_one_csv() -> None:
    with pytest.raises(SourceFormatError, match="not a zip"):
        french.collect(
            fetcher({**ZIPS, "49_Industry_Portfolios_CSV.zip": b"<html>"}), sources(), smoke=False
        )


def test_truncated_download_is_refused_and_files_are_kept(tmp_path: Path) -> None:
    run(tmp_path)
    before = snapshot(tmp_path)
    archive = "F-F_Research_Data_Factors_CSV.zip"
    truncated = variant(archive, "202211,    4.60,   -3.40,    1.38,    0.29\n", "")
    with pytest.raises(DataLossError, match="kf_us3_mkt_rf_m: 1 observation dates"):
        run(tmp_path, {**ZIPS, archive: truncated})
    assert snapshot(tmp_path) == before
