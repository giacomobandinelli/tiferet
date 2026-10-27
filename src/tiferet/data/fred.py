"""FRED and ALFRED adapter (plan §5.1; decisions 0013, 0014).

Every observation is fetched with its full vintage history: one row per value and real-time
period. A version is known at 23:59:59 New York time on its `realtime_start`, because the
data hold release dates but not release times. Observations that already existed when
ALFRED recorded a series' first vintage get an approximated `known_at`: the end of their
period plus the series' publication lag from config/sources.yaml, when that is earlier.

Smoke: `uv run python -m tiferet.data.fred --smoke`, with `FRED_API_KEY` in the environment.
"""

import json
from collections.abc import Sequence
from typing import Any

import numpy as np
import pandas as pd

from tiferet.data.adapter import Batch, combine, stamped
from tiferet.data.cli import main as run_cli
from tiferet.data.errors import SourceFormatError
from tiferet.data.fetch import Download, Fetcher, api_key
from tiferet.data.pit import DEVELOPMENT_END
from tiferet.data.schema import ACTUAL, APPROXIMATED
from tiferet.data.source_config import Fred, FredSeries, Sources
from tiferet.data.timing import NEW_YORK, end_of_day, known_after_lag

SOURCE = "fred"
REALTIME_START = "1776-07-04"
REALTIME_END = "9999-12-31"
SMOKE_END = DEVELOPMENT_END.date().isoformat()
"""Smoke requests stop here, so they touch no holdout data."""
PAGE_LIMIT = 100_000
MISSING = "."
PAGE_KEYS = ("count", "offset", "limit", "observations")
FIELDS = ("realtime_start", "realtime_end", "date", "value")


def _params(series: FredSeries, key: str, offset: int, *, smoke: bool) -> dict[str, str]:
    params = {
        "series_id": series.fred_id,
        "api_key": key,
        "file_type": "json",
        "realtime_start": REALTIME_START,
        "realtime_end": SMOKE_END if smoke else REALTIME_END,
        "limit": str(PAGE_LIMIT),
        "offset": str(offset),
    }
    if smoke:
        params["observation_end"] = SMOKE_END
    return params


def _page(content: bytes, item: str) -> dict[str, Any]:
    """One response page, with its structure checked."""
    try:
        page = json.loads(content)
    except (ValueError, UnicodeDecodeError):
        raise SourceFormatError(SOURCE, f"{item}: response is not JSON") from None
    if not isinstance(page, dict):
        raise SourceFormatError(SOURCE, f"{item}: response is not a JSON object")
    missing = [key for key in PAGE_KEYS if key not in page]
    if missing:
        raise SourceFormatError(SOURCE, f"{item}: response lacks {missing}")
    if not all(isinstance(page[k], int) for k in ("count", "offset", "limit")):
        raise SourceFormatError(SOURCE, f"{item}: count, offset and limit must be integers")
    if not isinstance(page["observations"], list):
        raise SourceFormatError(SOURCE, f"{item}: observations is not a list")
    return page


def download(
    fetcher: Fetcher, fred: Fred, series: FredSeries, key: str, *, smoke: bool
) -> list[Download]:
    """Every page of one series, following offsets until `count` rows have arrived."""
    downloads: list[Download] = []
    offset, count = 0, None
    while count is None or offset < count:
        result = fetcher.get(fred.api_url, _params(series, key, offset, smoke=smoke))
        downloads.append(result)
        page = _page(result.content, series.fred_id)
        if page["offset"] != offset:
            raise SourceFormatError(
                SOURCE, f"{series.fred_id}: page offset {page['offset']}, expected {offset}"
            )
        if count is not None and page["count"] != count:
            raise SourceFormatError(SOURCE, f"{series.fred_id}: count changed between pages")
        count = page["count"]
        received = len(page["observations"])
        if received == 0 and offset < count:
            raise SourceFormatError(
                SOURCE, f"{series.fred_id}: empty page at offset {offset} of {count}"
            )
        offset += received
    return downloads


def _observation(row: object, item: str) -> tuple[str, str, str]:
    if not isinstance(row, dict) or any(not isinstance(row.get(f), str) for f in FIELDS):
        raise SourceFormatError(SOURCE, f"{item}: an observation lacks one of {list(FIELDS)}")
    return row["date"], row["realtime_start"], row["value"]


def _dates(values: pd.Series, item: str, field: str) -> pd.Series:
    try:
        parsed: pd.Series = pd.to_datetime(values, format="%Y-%m-%d")
    except (ValueError, TypeError):
        raise SourceFormatError(SOURCE, f"{item}: {field} is not a YYYY-MM-DD date") from None
    return parsed


def parse(pages: Sequence[bytes], item: str) -> pd.DataFrame:
    """Rows `obs_date, realtime_start, value` of every page; missing values dropped."""
    parsed = [_page(content, item) for content in pages]
    if not parsed:
        raise SourceFormatError(SOURCE, f"{item}: no pages")
    rows = [_observation(row, item) for page in parsed for row in page["observations"]]
    if len(rows) != parsed[0]["count"]:
        raise SourceFormatError(
            SOURCE, f"{item}: {len(rows)} observations received, count says {parsed[0]['count']}"
        )
    frame = pd.DataFrame(rows, columns=["obs_date", "realtime_start", "value"], dtype=str)
    frame = frame[frame["value"] != MISSING]
    try:
        values = frame["value"].astype(float)
    except ValueError:
        raise SourceFormatError(SOURCE, f"{item}: a value is not a number") from None
    result = pd.DataFrame(
        {
            "obs_date": _dates(frame["obs_date"], item, "date"),
            "realtime_start": _dates(frame["realtime_start"], item, "realtime_start"),
            "value": values,
        }
    ).reset_index(drop=True)
    if result.duplicated(["obs_date", "realtime_start"]).any():
        raise SourceFormatError(SOURCE, f"{item}: duplicate observation and realtime_start")
    return result


def stamp(raw: pd.DataFrame, series: FredSeries) -> pd.DataFrame:
    """Silver rows: actual vintages, approximated before the series' first vintage."""
    actual = end_of_day(raw["realtime_start"], NEW_YORK)
    lag = series.publication_lag_days.value
    approximated = known_after_lag(raw["obs_date"], series.frequency, lag)
    use = (raw["realtime_start"] == raw["realtime_start"].min()) & (approximated < actual)
    known_at = actual.where(~use, approximated)
    kind = np.where(use, APPROXIMATED, ACTUAL)
    return stamped(series.series_id, raw["obs_date"], known_at, raw["value"], kind)


def collect(fetcher: Fetcher, sources: Sources, *, smoke: bool) -> Batch:
    key = api_key(sources.fred.api_key_env)
    downloads: list[Download] = []
    frames = []
    for series in sources.fred.series:
        pages = download(fetcher, sources.fred, series, key, smoke=smoke)
        raw = parse([page.content for page in pages], series.fred_id)
        frames.append(stamp(raw, series))
        downloads.extend(pages)
    expected = tuple(s.series_id for s in sources.fred.series)
    return Batch(SOURCE, tuple(downloads), combine(frames), expected)


if __name__ == "__main__":
    raise SystemExit(run_cli(SOURCE, collect))
