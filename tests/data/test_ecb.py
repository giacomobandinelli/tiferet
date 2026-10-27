"""Tests for the ECB Data Portal adapter on SDMX-JSON fixtures (decision 0013)."""

import json
from pathlib import Path

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
from tiferet.data import ecb
from tiferet.data.adapter import Batch, ingest
from tiferet.data.errors import SourceFormatError
from tiferet.data.source_config import Sources, load_sources

URLS = {
    "https://data-api.ecb.europa.eu/service/data/EXR/D.USD.EUR.SP00.A": fixture(
        "ecb", "exr_usd.json"
    ),
    "https://data-api.ecb.europa.eu/service/data/EST/B.EU000A2X2A25.WT": fixture(
        "ecb", "estr.json"
    ),
}


def sources() -> Sources:
    config = load_sources()
    datasets = []
    for dataset in config.ecb.datasets:
        series = tuple(s for s in dataset.series if s.series_id in {"ecb_exr_usd", "ecb_estr"})
        if series:
            datasets.append(dataset.model_copy(update={"series": series}))
    return config.model_copy(
        update={"ecb": config.ecb.model_copy(update={"datasets": tuple(datasets)})}
    )


def fetcher(urls: dict[str, bytes] = URLS) -> FakeFetcher:
    return FakeFetcher(lambda url, params: urls[url])


def run(tmp_path: Path, urls: dict[str, bytes] = URLS) -> Batch:
    batch = ecb.collect(fetcher(urls), sources(), smoke=False)
    ingest(batch, silver_roots(tmp_path), tmp_path / "bronze", max_row_loss=0.001)
    return batch


def test_reference_rate_known_at_publication_time() -> None:
    silver = ecb.collect(fetcher(), sources(), smoke=False).silver
    usd = silver[silver["series_id"] == "ecb_exr_usd"]
    assert len(usd) == 3  # the null observation is dropped
    assert known_at(usd, "2022-04-14") == [utc("2022-04-14 15:00")]  # 17:00 CEST
    assert known_at(usd, "2022-12-30") == [utc("2022-12-30 16:00")]  # 17:00 CET
    assert set(usd["vintage_kind"]) == {"approximated"}


def test_estr_known_on_the_next_target_day() -> None:
    silver = ecb.collect(fetcher(), sources(), smoke=False).silver
    estr = silver[silver["series_id"] == "ecb_estr"]
    # Good Friday and Easter Monday 2022 are TARGET holidays.
    assert known_at(estr, "2022-04-14") == [utc("2022-04-19 07:00")]
    assert known_at(estr, "2022-12-29") == [utc("2022-12-30 08:00")]
    assert known_at(estr, "2022-12-30") == [utc("2023-01-02 08:00")]


def test_output_passes_pit_get_and_respects_the_holdout_boundary(tmp_path: Path) -> None:
    run(tmp_path)
    roots = silver_roots(tmp_path)
    assert_partitioned(roots)
    assert len(research_view(roots, "ecb_estr")) == 2
    assert len(production_view(roots, "ecb_estr")) == 3
    assert len(research_view(roots, "ecb_exr_usd")) == 3


def test_requests_ask_for_sdmx_json_and_smoke_stops_at_2022() -> None:
    fake = fetcher()
    ecb.collect(fake, sources(), smoke=True)
    assert all(p["format"] == "jsondata" and p["endPeriod"] == "2022-12-31" for _, p in fake.calls)


def changed(name: str, edit: str) -> bytes:
    message = json.loads(fixture("ecb", name))
    structure = message["structure"]["dimensions"]
    if edit == "renamed-dimension":
        structure["observation"][0]["id"] = "TIME"
    elif edit == "missing-structure":
        del message["structure"]
    elif edit == "other-series":
        structure["series"][1]["values"][0]["id"] = "GBP"
    elif edit == "two-series":
        series = message["dataSets"][0]["series"]
        series["0:0:0:0:1"] = series["0:0:0:0:0"]
    return json.dumps(message).encode()


@pytest.mark.parametrize(
    ("edit", "message"),
    [
        ("renamed-dimension", "no TIME_PERIOD dimension"),
        ("missing-structure", "lacks structure"),
        ("other-series", "different series"),
        ("two-series", "exactly one series"),
    ],
)
def test_format_errors_write_nothing(tmp_path: Path, edit: str, message: str) -> None:
    run(tmp_path)
    before = snapshot(tmp_path)
    url = next(u for u in URLS if "EXR" in u)
    with pytest.raises(SourceFormatError, match=f"ecb: .*{message}"):
        run(tmp_path, {**URLS, url: changed("exr_usd.json", edit)})
    assert snapshot(tmp_path) == before
