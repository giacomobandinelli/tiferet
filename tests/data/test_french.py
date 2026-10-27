"""Tests for the Kenneth French Data Library adapter on zipped CSV fixtures (decision 0013)."""

from pathlib import Path

import pandas as pd
import pytest

from data.adapter_support import (
    FakeFetcher,
    assert_partitioned,
    fixture,
    french_variant,
    known_at,
    production_view,
    research_view,
    silver_roots,
    snapshot,
    utc,
)
from fixtures.build_sources import FRENCH_ZIPS
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
        (
            "F-F_Research_Data_Factors_CSV.zip",
            ",Mkt-RF,SMB",
            ",MktRF,SMB",
            "no table with monthly dates",
        ),
        (
            "49_Industry_Portfolios_CSV.zip",
            "Average Value Weighted Returns -- Monthly",
            "Average Value Weighted Returns -- Quarterly",
            "0 tables titled",
        ),
        (
            "49_Industry_Portfolios_CSV.zip",
            "Average Equal Weighted Returns -- Monthly",
            "Average Value Weight Returns -- Monthly",
            "2 tables titled",
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
        run(tmp_path, {**ZIPS, archive: french_variant(archive, old, new)})
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
    truncated = french_variant(archive, "202211,    4.60,   -3.40,    1.38,    0.29\n", "")
    with pytest.raises(DataLossError, match="kf_us3_mkt_rf_m: 1 observation dates"):
        run(tmp_path, {**ZIPS, archive: truncated})
    assert snapshot(tmp_path) == before


def ids_of(silver: pd.DataFrame, prefix: str) -> set[str]:
    return {s for s in silver["series_id"].unique() if s.startswith(prefix)}


def test_size_deciles_are_one_series_whatever_the_file_calls_them() -> None:
    """The monthly file writes "2-Dec", the daily file "Dec 2"; both are `dec2`."""
    silver = french.collect(fetcher(), sources(), smoke=False).silver
    deciles = ["lo10", *(f"dec{n}" for n in range(2, 10)), "hi10"]
    assert ids_of(silver, "kf_size_") == {
        f"kf_size_{key}_{frequency}" for key in deciles for frequency in "md"
    }
    assert len(series(silver, "kf_size_dec2_m")) == 4
    assert len(series(silver, "kf_size_dec2_d")) == 4


def test_unused_size_columns_are_not_series() -> None:
    silver = french.collect(fetcher(), sources(), smoke=False).silver
    assert not ids_of(silver, "kf_size_le0") | ids_of(silver, "kf_size_qnt")


@pytest.mark.parametrize(
    ("archive", "series_id"),
    [
        ("Portfolios_Formed_on_ME_CSV.zip", "kf_size_hi10_m"),  # "Weight", leading "Average"
        ("Portfolios_Formed_on_ME_Daily_CSV.zip", "kf_size_hi10_d"),  # "Weighted"
        ("49_Industry_Portfolios_daily_CSV.zip", "kf_ind49_agric_d"),
    ],
)
def test_returns_tables_are_found_by_their_real_titles(archive: str, series_id: str) -> None:
    silver = french.collect(fetcher(), sources(), smoke=False).silver
    assert len(series(silver, series_id)) > 0


@pytest.mark.parametrize(
    ("archive", "old", "new", "series_id"),
    [
        (
            "49_Industry_Portfolios_CSV.zip",
            "Average Value Weighted Returns -- Monthly",
            "  VALUE   weight  returns -- MONTHLY ",
            "kf_ind49_agric_m",
        ),
        (
            "Portfolios_Formed_on_ME_CSV.zip",
            "Average Value Weight Returns -- Monthly",
            "Value Weighted Returns -- Monthly",
            "kf_size_dec2_m",
        ),
    ],
    ids=["case-and-spaces", "weighted-without-average"],
)
def test_title_wording_does_not_matter(archive: str, old: str, new: str, series_id: str) -> None:
    zips = {**ZIPS, archive: french_variant(archive, old, new)}
    silver = french.collect(fetcher(zips), sources(), smoke=False).silver
    assert len(series(silver, series_id)) > 0


def test_equal_weighted_tables_are_not_taken_for_value_weighted() -> None:
    archive = "49_Industry_Portfolios_CSV.zip"
    zips = {
        **ZIPS,
        archive: french_variant(
            archive,
            "Average Value Weighted Returns -- Monthly",
            "Average Value Weighted Returns -- Quarterly",
        ),
    }
    with pytest.raises(SourceFormatError, match="0 tables titled"):
        french.collect(fetcher(zips), sources(), smoke=False)


@pytest.mark.parametrize(
    "header",
    [
        ",Mkt-RF,SMB,HML,RF",
        "  ,Mkt-RF,SMB,HML,RF  ",
        "Mkt-RF,SMB,HML,RF",
        "  Mkt-RF , SMB , HML , RF   ",
        "\t,Mkt-RF,SMB,HML,RF\t",
    ],
    ids=["comma", "padded", "no-comma", "no-comma-padded", "tabs"],
)
@pytest.mark.parametrize(
    "archive", ["F-F_Research_Data_Factors_CSV.zip", "F-F_Research_Data_Factors_daily_CSV.zip"]
)
def test_header_line_tolerates_whitespace_and_a_missing_comma(archive: str, header: str) -> None:
    expected = french.collect(fetcher(), sources(), smoke=False).silver
    zips = {**ZIPS, archive: french_variant(archive, "\n,Mkt-RF,SMB,HML,RF\n", f"\n{header}\n")}
    silver = french.collect(fetcher(zips), sources(), smoke=False).silver
    pd.testing.assert_frame_equal(silver, expected)


def test_factor_table_has_no_title_and_follows_a_note() -> None:
    archive = "F-F_Research_Data_Factors_CSV.zip"
    text = french.read_csv(ZIPS[archive], archive)
    found = french.tables(text)
    assert [t.title for t in found] == [
        "Missing data are indicated by -99.99.",
        "Annual Factors: January-December",
    ]
    assert [t.date_width for t in found] == [6, 4]


def test_the_first_factor_table_of_the_right_width_is_used() -> None:
    archive = "Asia_Pacific_ex_Japan_3_Factors_Daily_CSV.zip"
    extra = "\n,Mkt-RF,SMB,HML,RF\n20221101,    9.00,    9.00,    9.00,    9.00\n"
    zips = {**ZIPS, archive: french_variant(archive, "\nCopyright", f"{extra}\nCopyright")}
    silver = french.collect(fetcher(zips), sources(), smoke=False).silver
    assert len(series(silver, "kf_apxj3_smb_d")) == 4
    assert series(silver, "kf_apxj3_smb_d")["value"].max() < 0.05


def test_headerless_factor_table_is_refused_and_named() -> None:
    archive = "Asia_Pacific_ex_Japan_3_Factors_Daily_CSV.zip"
    zips = {**ZIPS, archive: french_variant(archive, ",Mkt-RF,SMB,HML,RF\n", "")}
    with pytest.raises(SourceFormatError, match="1 tables of that date width have no header"):
        french.collect(fetcher(zips), sources(), smoke=False)


def test_header_of_the_wrong_width_is_not_taken_for_a_header() -> None:
    archive = "Asia_Pacific_ex_Japan_3_Factors_Daily_CSV.zip"
    zips = {**ZIPS, archive: french_variant(archive, ",Mkt-RF,SMB,HML,RF\n", "Mkt-RF,SMB,HML\n")}
    with pytest.raises(SourceFormatError, match="no table with daily dates"):
        french.collect(fetcher(zips), sources(), smoke=False)


@pytest.mark.parametrize(
    ("archive", "old", "new", "message"),
    [
        ("Portfolios_Formed_on_ME_CSV.zip", "Hi 10", "Hi/10", r"unknown column headers \['Hi/10'"),
        ("Portfolios_Formed_on_ME_CSV.zip", ",Hi 10", ",Hi (10)", "unknown column headers"),
        ("Portfolios_Formed_on_ME_Daily_CSV.zip", "Dec 3", "Dec 2", r"repeat \['dec2'\]"),
        ("Portfolios_Formed_on_ME_CSV.zip", ",3-Dec", ",Dec 2", r"repeat \['dec2'\]"),
        ("Portfolios_Formed_on_ME_CSV.zip", "Lo 10,", "Lo 11,", "lacks columns"),
    ],
    ids=["slash", "bracket", "repeated-daily", "alias-collides", "missing-column"],
)
def test_unknown_or_repeated_headers_are_refused(
    archive: str, old: str, new: str, message: str
) -> None:
    zips = {**ZIPS, archive: french_variant(archive, old, new)}
    with pytest.raises(SourceFormatError, match=f"french: .*{message}"):
        french.collect(fetcher(zips), sources(), smoke=False)
