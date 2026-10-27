"""Tests for FRED series without usable vintages, and for FRED inspect mode (decision 0013).

The error fixtures follow FRED's documented JSON error format (`error_code`, `error_message`).
The adapter recognises the two no-vintage answers by the phrases in config/sources.yaml.
"""

import json
from pathlib import Path

import pandas as pd
import pytest

from data.adapter_support import (
    FakeFetcher,
    assert_partitioned,
    fixture,
    known_at,
    research_view,
    silver_roots,
    utc,
)
from tiferet.data import fred
from tiferet.data.adapter import ingest
from tiferet.data.fetch import FetchError
from tiferet.data.source_config import Sources, load_sources

KEY = "fixture-key-0123456789abcdef"
TODAY = "2026-10-27"
TODAY_END = utc("2026-10-28 03:59:59")  # 23:59:59 New York (EDT)


def sources() -> Sources:
    config = load_sources()
    series = tuple(s for s in config.fred.series if s.fred_id in {"CPIAUCSL", "DGS3MO"})
    return config.model_copy(update={"fred": config.fred.model_copy(update={"series": series})})


def error(name: str) -> FetchError:
    """The FetchError `HttpFetcher` raises for a FRED 400 with this body."""
    detail = " ".join(json.loads(fixture("fred", name))["error_message"].split())
    return FetchError(f"HTTP 400: {detail}", status=400, detail=detail)


def revised_snapshot() -> bytes:
    """Today's snapshot: one value revised since 2022 and one observation added."""
    page = json.loads(fixture("fred", "dgs3mo_snapshot.json"))
    page["observations"][0]["value"] = "4.33"
    page["observations"].append({**page["observations"][-1], "date": "2022-12-30", "value": "4.42"})
    page["count"] = len(page["observations"])
    return json.dumps(page).encode()


def fetcher(dgs3mo_error: str, *, development_snapshot: bool = True) -> FakeFetcher:
    def respond(url: str, params: dict[str, str]) -> bytes:
        if params["series_id"] == "CPIAUCSL":
            return fixture("fred", "cpiaucsl.json")
        if params["realtime_start"] == fred.REALTIME_START:
            raise error(dgs3mo_error)
        if params["realtime_start"] == fred.DEVELOPMENT_DAY:
            if not development_snapshot:
                raise error(dgs3mo_error)
            return fixture("fred", "dgs3mo_snapshot.json")
        assert params["realtime_start"] == params["realtime_end"] == TODAY
        return revised_snapshot()

    return FakeFetcher(respond)


@pytest.fixture(autouse=True)
def _environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FRED_API_KEY", KEY)
    monkeypatch.setattr(fred, "today", lambda: TODAY)


def snapshot_calls(fake: FakeFetcher) -> list[dict[str, str]]:
    return [p for _, p in fake.calls if p["realtime_start"] != fred.REALTIME_START]


def dgs3mo(frame: pd.DataFrame) -> pd.DataFrame:
    return frame[frame["series_id"] == "fred_dgs3mo"]


@pytest.mark.parametrize("answer", ["error_not_in_alfred.json", "error_vintage_limit.json"])
def test_smoke_uses_the_development_end_snapshot_stamped_by_the_lag(answer: str) -> None:
    fake = fetcher(answer)
    silver = fred.collect(fake, sources(), smoke=True).silver
    calls = snapshot_calls(fake)
    assert [(p["realtime_start"], p["realtime_end"]) for p in calls] == [("2022-12-31",) * 2]
    rows = dgs3mo(silver)
    assert len(rows) == 3  # the missing value is dropped
    assert set(rows["vintage_kind"]) == {"approximated"}
    # Period end plus the 4-day lag, 23:59:59 New York.
    assert known_at(rows, "2022-12-20") == [utc("2022-12-25 04:59:59")]
    assert known_at(rows, "2022-12-27") == [utc("2023-01-01 04:59:59")]
    assert "actual" in set(silver.loc[silver["series_id"] == "fred_cpiaucsl", "vintage_kind"])


def test_ingest_adds_only_new_or_changed_values_of_todays_snapshot() -> None:
    fake = fetcher("error_vintage_limit.json")
    rows = dgs3mo(fred.collect(fake, sources(), smoke=False).silver)
    assert [p["realtime_start"] for p in snapshot_calls(fake)] == ["2022-12-31", TODAY]
    assert all("observation_end" not in p for p in snapshot_calls(fake))
    # The 2022 value by the lag, its later revision from today; the unchanged values once.
    assert known_at(rows, "2022-12-20") == [utc("2022-12-25 04:59:59"), TODAY_END]
    assert known_at(rows, "2022-12-27") == [utc("2023-01-01 04:59:59")]
    assert known_at(rows, "2022-12-30") == [TODAY_END]


def test_refused_development_snapshot_falls_back_to_today_never_before_it() -> None:
    fake = fetcher("error_not_in_alfred.json", development_snapshot=False)
    rows = dgs3mo(fred.collect(fake, sources(), smoke=True).silver)
    today_calls = [p for p in snapshot_calls(fake) if p["realtime_start"] == TODAY]
    assert today_calls and all(p["observation_end"] == "2022-12-31" for p in today_calls)
    assert set(rows["known_at"]) == {TODAY_END}


def test_snapshot_rows_respect_the_holdout_boundary(tmp_path: Path) -> None:
    batch = fred.collect(fetcher("error_vintage_limit.json"), sources(), smoke=False)
    roots = silver_roots(tmp_path)
    ingest(batch, roots, tmp_path / "bronze", max_row_loss=0.001)
    assert_partitioned(roots)
    view = research_view(roots, "fred_dgs3mo")
    assert list(view["value"]) == [4.32]  # the revision of 2026 stays out of research


def test_first_vintage_after_the_development_end_is_never_backdated() -> None:
    series = sources().fred.by_id("fred_cpiaucsl")
    raw = pd.DataFrame(
        {
            "obs_date": pd.to_datetime(["1990-01-01", "1990-02-01"]),
            "realtime_start": pd.to_datetime(["2024-03-12", "2024-03-12"]),
            "value": [127.0, 127.5],
        }
    )
    stamped = fred.stamp(raw, series)
    assert set(stamped["vintage_kind"]) == {"actual"}
    assert set(stamped["known_at"]) == {utc("2024-03-13 03:59:59")}


def test_other_400_errors_stay_errors_with_the_server_text() -> None:
    with pytest.raises(FetchError, match="api_key is not registered"):
        fred.collect(fetcher("error_bad_key.json"), sources(), smoke=True)


def test_inspect_probes_both_ranges_without_values() -> None:
    fake = fetcher("error_vintage_limit.json")
    inspection = fred.inspect(fake, sources())
    assert inspection.failures == 0
    assert inspection.lines[:2] == [
        "CPIAUCSL: vintages to 2022-12-31: ok, 7 rows",
        "CPIAUCSL: all vintages: ok, 7 rows",
    ]
    assert inspection.lines[3].startswith("DGS3MO: all vintages: failed, snapshot fallback")
    assert "maximum number of vintage dates" in inspection.lines[3]
    assert all(p["observation_end"] == "2022-12-31" for _, p in fake.calls)
    text = "\n".join(inspection.lines)
    for page in ("cpiaucsl.json", "dgs3mo_snapshot.json"):
        for row in json.loads(fixture("fred", page))["observations"]:
            assert row["date"] not in text
            assert row["value"] == "." or row["value"] not in text


def test_inspect_counts_errors_without_fallback_as_failures() -> None:
    inspection = fred.inspect(fetcher("error_bad_key.json"), sources())
    assert inspection.failures == 2
    assert "failed, no fallback" in inspection.lines[2]
