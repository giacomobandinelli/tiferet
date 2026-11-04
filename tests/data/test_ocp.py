"""Tests for the Open Crypto Pricing adapter on CSV fixtures (decisions 0013, 0017)."""

import re
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
from tiferet.data import ocp
from tiferet.data.adapter import Batch, ingest
from tiferet.data.errors import DataLossError, SourceFormatError
from tiferet.data.source_config import ReturnUnits, Sources, load_sources

BASE = "https://opencryptopricing.com/data/"
DAILY = "daily/factors_cg_investable_daily.csv"
WEEKLY = "factors_cg_investable.csv"
MONTHLY = "monthly/factors_cg_investable_monthly.csv"
FILES = {path: fixture("ocp", path.rsplit("/", 1)[-1]) for path in (DAILY, WEEKLY, MONTHLY)}


def fetcher(files: dict[str, bytes] = FILES) -> FakeFetcher:
    return FakeFetcher(lambda url, params: files[url.removeprefix(BASE)])


def replaced(path: str, old: str, new: str) -> dict[str, bytes]:
    text = FILES[path].decode()
    assert old in text
    return {**FILES, path: text.replace(old, new).encode()}


def run(tmp_path: Path, files: dict[str, bytes] = FILES, config: Sources | None = None) -> Batch:
    batch = ocp.collect(fetcher(files), config or load_sources(), smoke=False)
    ingest(batch, silver_roots(tmp_path), tmp_path / "bronze", max_row_loss=0.001)
    return batch


def series(silver: pd.DataFrame, series_id: str) -> pd.Series:
    return silver[silver["series_id"] == series_id].set_index("obs_date")["value"]


def test_factors_and_counts_of_each_frequency() -> None:
    silver = ocp.collect(fetcher(), load_sources(), smoke=False).silver
    assert {s for s in silver["series_id"].unique() if s.endswith("_w")} == {
        "ocp_cg_cmkt_w",
        "ocp_cg_csize_w",
        "ocp_cg_cmom_w",
        "ocp_cg_rf_w",
        "ocp_cg_n_coins_w",
    }
    weekly = series(silver, "ocp_cg_cmkt_w")
    assert list(weekly.index) == list(pd.to_datetime(["2022-11-28", "2022-12-05", "2022-12-12"]))
    assert weekly.iloc[0] == pytest.approx(0.0412)
    assert len(series(silver, "ocp_cg_cmom_d")) == 2  # an empty field is missing
    assert series(silver, "ocp_cg_n_coins_d").iloc[0] == 812
    monthly = series(silver, "ocp_cg_cmkt_m")
    assert list(monthly.index) == list(pd.to_datetime(["2022-10-01", "2022-11-01", "2022-12-01"]))
    assert set(silver["vintage_kind"]) == {"approximated"}


def test_known_fourteen_days_after_the_end_of_the_period() -> None:
    silver = ocp.collect(fetcher(), load_sources(), smoke=False).silver
    rows = silver[silver["series_id"] == "ocp_cg_cmkt_w"]
    assert known_at(rows, "2022-12-05") == [utc("2022-12-25 23:59:59")]
    rows = silver[silver["series_id"] == "ocp_cg_cmkt_m"]
    assert known_at(rows, "2022-11-01") == [utc("2022-12-14 23:59:59")]
    rows = silver[silver["series_id"] == "ocp_cg_cmkt_d"]
    assert known_at(rows, "2022-12-17") == [utc("2022-12-31 23:59:59")]


def test_output_passes_pit_get_and_respects_the_holdout_boundary(tmp_path: Path) -> None:
    run(tmp_path)
    roots = silver_roots(tmp_path)
    assert_partitioned(roots)
    assert len(research_view(roots, "ocp_cg_cmkt_w")) == 2
    assert len(production_view(roots, "ocp_cg_cmkt_w")) == 3
    assert len(research_view(roots, "ocp_cg_cmkt_d")) == 2  # 17 December is known 1 January
    assert len(research_view(roots, "ocp_cg_cmkt_m")) == 2


def test_percent_returns_are_divided_by_100_and_counts_are_not() -> None:
    config = load_sources()
    files = tuple(
        f.model_copy(update={"return_units": ReturnUnits.PERCENT}) for f in config.ocp.files.value
    )
    listed = config.ocp.files.model_copy(update={"value": files})
    changed = config.model_copy(update={"ocp": config.ocp.model_copy(update={"files": listed})})
    silver = ocp.collect(fetcher(), changed, smoke=False).silver
    assert series(silver, "ocp_cg_cmkt_w").iloc[0] == pytest.approx(0.000412)
    assert series(silver, "ocp_cg_n_coins_w").iloc[0] == 790


def test_the_monthly_file_has_no_coin_count_and_its_label_is_not_read() -> None:
    files = replaced(MONTHLY, "2022-10,", "October,")
    silver = ocp.collect(fetcher(files), load_sources(), smoke=False).silver
    assert series(silver, "ocp_cg_cmkt_m").index[0] == pd.Timestamp("2022-10-01")
    assert "ocp_cg_n_coins_m" not in set(silver["series_id"])


def test_the_period_of_a_month_ends_on_month_end() -> None:
    silver = ocp.collect(fetcher(), load_sources(), smoke=False).silver
    rows = silver[silver["series_id"] == "ocp_cg_cmkt_m"]
    # 2022-10-31 + 14 days; a month-start based rule would give 2022-10-15.
    assert known_at(rows, "2022-10-01") == [utc("2022-11-14 23:59:59")]


@pytest.mark.parametrize(
    ("path", "old", "new", "message"),
    [
        (DAILY, "date,CMKT", "day,CMKT", "header is day, CMKT, .*; expected date, CMKT"),
        (WEEKLY, "CMOM,RF", "RF,CMOM", "header is .*RF, CMOM"),
        (WEEKLY, "2022-12-05,2022-12-11", "2022-12-05,2022-12-12", "does not span seven days"),
        (WEEKLY, "2022-12-12,2022-12-18", "2022-12-12,2022-12-18,1", "width differs"),
        (DAILY, "2022-12-16,", "2022-12-15,", "not unique and increasing"),
        (DAILY, "2022-12-16,", "16.12.2022,", "date is not a YYYY-MM-DD date"),
        (DAILY, "2022-12-16,", "2022-02-30,", "date is not a valid date"),
        (MONTHLY, "2022-11-01,", "2022-11-x1,", "month_start is not a YYYY-MM-DD date"),
        (MONTHLY, "2022-11-30", "2022-11-29", "does not run from its first to its last day"),
        (MONTHLY, "2022-11-01,2022-11-30", "2022-11-02,2022-11-30", "does not run from its first"),
        (DAILY, ",0.0001,815", ",0.0001,815.5", "n_coins value is not a count"),
        (DAILY, "0.0087,", "n/a,", "CMKT value is not a number"),
        (DAILY, "0.0087,", "inf,", "CMKT value is not finite"),
        (DAILY, "2022-12-16,-0.0211,0.0032,,0.0001,815", ",,,,,", "date is not a YYYY-MM-DD"),
    ],
    ids=[
        "date-column",
        "order",
        "week",
        "width",
        "dates",
        "date-format",
        "date-invalid",
        "month-start",
        "month-end",
        "month-span",
        "count",
        "number",
        "finite",
        "empty-row",
    ],
)
def test_format_errors_name_the_file_and_write_nothing(
    tmp_path: Path, path: str, old: str, new: str, message: str
) -> None:
    run(tmp_path)
    before = snapshot(tmp_path)
    with pytest.raises(SourceFormatError, match=f"ocp: {re.escape(path)}: .*{message}"):
        run(tmp_path, replaced(path, old, new))
    assert snapshot(tmp_path) == before


def test_empty_file_is_refused() -> None:
    with pytest.raises(SourceFormatError, match="the file is empty"):
        ocp.collect(fetcher({**FILES, DAILY: b"\n"}), load_sources(), smoke=False)


def test_a_column_of_missing_values_is_refused_by_name(tmp_path: Path) -> None:
    text = "week_start,week_end,CMKT,CSIZE,CMOM,RF,n_coins\n2022-11-28,2022-12-04,0.1,0.1,,0.1,1\n"
    batch = ocp.collect(fetcher({**FILES, WEEKLY: text.encode()}), load_sources(), smoke=False)
    with pytest.raises(DataLossError, match=r"ocp_cg_cmom_w \(all values missing\)"):
        ingest(batch, silver_roots(tmp_path), tmp_path / "bronze", max_row_loss=0.001)


def test_inspect_prints_headers_and_row_counts_only() -> None:
    inspection = ocp.inspect(fetcher(), load_sources())
    assert inspection.failures == 0
    assert inspection.lines[:2] == [
        f"{DAILY}: 3 rows",
        "  header (as configured): date, CMKT, CSIZE, CMOM, RF, n_coins",
    ]
    text = "\n".join(inspection.lines)
    tokens = {t for c in FILES.values() for t in re.findall(r"[\d.\-]+", c.decode()) if t != "-"}
    assert not [t for t in tokens if re.search(rf"(?<![\w.\-]){re.escape(t)}(?![\w.\-])", text)]


def test_inspect_flags_a_header_that_differs() -> None:
    files = replaced(MONTHLY, "month_end,CMKT", "month_stop,CMKT")
    inspection = ocp.inspect(fetcher(files), load_sources())
    assert inspection.failures == 1
    assert (
        "  header (differs from the configuration): "
        "month, month_start, month_stop, CMKT, CSIZE, CMOM, RF" in inspection.lines
    )
