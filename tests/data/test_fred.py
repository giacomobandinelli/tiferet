"""Tests for the FRED and ALFRED adapter on fixtures (decision 0013)."""

import json
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
from tiferet.data import fred
from tiferet.data.adapter import Batch, ingest, smoke_lines
from tiferet.data.bronze import read_manifest
from tiferet.data.errors import SourceFormatError
from tiferet.data.source_config import Sources, load_sources

KEY = "fixture-key-0123456789abcdef"
PAGES = {
    ("CPIAUCSL", "0"): fixture("fred", "cpiaucsl.json"),
    ("DGS10", "0"): fixture("fred", "dgs10_page1.json"),
    ("DGS10", "3"): fixture("fred", "dgs10_page2.json"),
}


def sources() -> Sources:
    config = load_sources()
    series = tuple(s for s in config.fred.series if s.fred_id in {"CPIAUCSL", "DGS10"})
    return config.model_copy(update={"fred": config.fred.model_copy(update={"series": series})})


def fetcher(pages: dict[tuple[str, str], bytes] = PAGES) -> FakeFetcher:
    return FakeFetcher(lambda url, params: pages[(params["series_id"], params["offset"])])


@pytest.fixture(autouse=True)
def _key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FRED_API_KEY", KEY)


def run(tmp_path: Path, pages: dict[tuple[str, str], bytes] = PAGES) -> Batch:
    batch = fred.collect(fetcher(pages), sources(), smoke=False)
    ingest(batch, silver_roots(tmp_path), tmp_path / "bronze", max_row_loss=0.001)
    return batch


def test_vintages_are_actual_and_pre_vintage_rows_approximated() -> None:
    silver = fred.collect(fetcher(), sources(), smoke=False).silver
    cpi = silver[silver["series_id"] == "fred_cpiaucsl"]
    # First vintage 1990-05-15; Dec 1989 ends 45 days before 1990-02-14, so it is approximated.
    assert known_at(cpi, "1989-12-01") == [utc("1990-02-15 04:59:59"), utc("1991-01-12 04:59:59")]
    kinds = cpi.set_index(["obs_date", "known_at"])["vintage_kind"]
    assert kinds.loc[(pd.Timestamp("1989-12-01"), utc("1990-02-15 04:59:59"))] == "approximated"
    assert kinds.loc[(pd.Timestamp("1989-12-01"), utc("1991-01-12 04:59:59"))] == "actual"
    # March 1990 plus 45 days is the first vintage itself, which is not earlier: actual.
    assert known_at(cpi, "1990-03-01") == [utc("1990-05-16 03:59:59")]
    assert set(cpi.loc[cpi["obs_date"] >= "1990-03-01", "vintage_kind"]) == {"actual"}


def test_missing_values_are_dropped() -> None:
    silver = fred.collect(fetcher(), sources(), smoke=False).silver
    assert pd.Timestamp("1990-02-01") not in set(silver["obs_date"])
    assert pd.Timestamp("2022-05-30") not in set(silver["obs_date"])


def test_daily_series_pages_and_lag() -> None:
    fake = fetcher()
    silver = fred.collect(fake, sources(), smoke=False).silver
    dgs10 = silver[silver["series_id"] == "fred_dgs10"]
    assert [c[1]["offset"] for c in fake.calls if c[1]["series_id"] == "DGS10"] == ["0", "3"]
    assert len(dgs10) == 4
    assert known_at(dgs10, "2022-05-27") == [utc("2022-06-01 03:59:59")]
    assert known_at(dgs10, "2022-05-31") == [utc("2022-06-02 03:59:59")]
    assert list(dgs10["vintage_kind"]) == ["approximated", "approximated", "actual", "actual"]


def test_output_passes_pit_get_and_respects_the_holdout_boundary(tmp_path: Path) -> None:
    run(tmp_path)
    roots = silver_roots(tmp_path)
    assert_partitioned(roots)
    view = research_view(roots, "fred_cpiaucsl")
    nov = view[view["obs_date"] == pd.Timestamp("2022-11-01")]
    # The revision of 2022-12-31 is known 23:59:59 New York, 05:59:59 Berlin on 1 January.
    assert list(nov["value"]) == [300.0]
    assert (roots.holdout / "fred_cpiaucsl.parquet").is_file()
    later = production_view(roots, "fred_cpiaucsl")
    assert list(later.loc[later["obs_date"] == pd.Timestamp("2022-11-01"), "value"]) == [300.5]
    assert not research_view(roots, "fred_dgs10").empty


def test_key_never_reaches_output_or_manifest(tmp_path: Path) -> None:
    batch = run(tmp_path)
    manifest = read_manifest(tmp_path / "bronze")
    assert len(manifest) == 3
    assert all(KEY not in url and "api_key=REDACTED" in url for url in manifest["url"])
    assert all(KEY not in line for line in smoke_lines(batch))


def test_smoke_caps_requests_at_the_development_end() -> None:
    fake = fetcher()
    fred.collect(fake, sources(), smoke=True)
    assert all(p["realtime_end"] == p["observation_end"] == "2022-12-31" for _, p in fake.calls)


def edited(name: str, change: dict[str, object]) -> bytes:
    page = json.loads(fixture("fred", name))
    page.update(change)
    return json.dumps(page).encode()


def renamed_field(name: str) -> bytes:
    page = json.loads(fixture("fred", name))
    page["observations"][0]["observation_date"] = page["observations"][0].pop("date")
    return json.dumps(page).encode()


@pytest.mark.parametrize(
    ("pages", "message"),
    [
        ({**PAGES, ("CPIAUCSL", "0"): renamed_field("cpiaucsl.json")}, "lacks one of"),
        (
            {**PAGES, ("DGS10", "3"): edited("dgs10_page2.json", {"observations": []})},
            "empty page at offset 3 of 5",
        ),
        ({**PAGES, ("CPIAUCSL", "0"): edited("cpiaucsl.json", {"count": 6})}, "count says"),
        ({**PAGES, ("DGS10", "3"): edited("dgs10_page2.json", {"count": 6})}, "count changed"),
        ({**PAGES, ("DGS10", "3"): edited("dgs10_page2.json", {"offset": 2})}, "page offset"),
        ({**PAGES, ("CPIAUCSL", "0"): b"<html>busy</html>"}, "not JSON"),
    ],
    ids=["renamed-field", "missing-page", "count-mismatch", "count-changed", "offset", "html"],
)
def test_format_errors_write_nothing(
    tmp_path: Path, pages: dict[tuple[str, str], bytes], message: str
) -> None:
    run(tmp_path)
    before = snapshot(tmp_path)
    with pytest.raises(SourceFormatError, match=f"fred: .*{message}"):
        run(tmp_path, pages)
    assert snapshot(tmp_path) == before
