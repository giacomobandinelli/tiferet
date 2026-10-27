"""FRED and ALFRED adapter (plan §5.1; decisions 0013, 0014).

Every observation is fetched with its full vintage history: one row per value and real-time
period. A version is known at 23:59:59 New York time on its `realtime_start`, because the
data hold release dates but not release times. Observations that already existed when
ALFRED recorded a series' first vintage get an approximated `known_at`: the end of their
period plus the series' publication lag from config/sources.yaml, when that is earlier.

When FRED answers 400 with an error text containing a phrase of `fred.no_vintage_errors` (no
ALFRED history, or more vintage dates than one request allows), the series is fetched from
real-time snapshots instead (`snapshot_rows`). Any other error stays an error. Lag-based dates
come only from a vintage or snapshot taken by the development end.

Smoke: `uv run python -m tiferet.data.fred --smoke`, with `FRED_API_KEY` in the environment.
"""

import json
from collections.abc import Sequence
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from tiferet.data.adapter import Batch, combine, stamped
from tiferet.data.cli import Inspection
from tiferet.data.cli import main as run_cli
from tiferet.data.errors import SourceFormatError
from tiferet.data.fetch import Download, Fetcher, FetchError, api_key
from tiferet.data.pit import DEVELOPMENT_END
from tiferet.data.schema import ACTUAL, APPROXIMATED
from tiferet.data.source_config import Fred, FredSeries, Sources
from tiferet.data.timing import NEW_YORK, end_of_day, known_after_lag

SOURCE = "fred"
REALTIME_START = "1776-07-04"
REALTIME_END = "9999-12-31"
DEVELOPMENT_DAY = DEVELOPMENT_END.date().isoformat()
SMOKE_END = DEVELOPMENT_DAY
"""Smoke requests stop here, so they touch no holdout data."""
PAGE_LIMIT = 100_000
MISSING = "."
PAGE_KEYS = ("count", "offset", "limit", "observations")
FIELDS = ("realtime_start", "realtime_end", "date", "value")
FRED_ZONE = ZoneInfo("America/Chicago")
"""FRED's own time zone, which sets its "today" for real-time dates."""


def today() -> str:
    """Today's date in FRED's time zone: the real-time date of an ingestion snapshot."""
    return pd.Timestamp.now(tz=FRED_ZONE).date().isoformat()


def _params(
    series: FredSeries, key: str, offset: int, *, smoke: bool, snapshot: str | None = None
) -> dict[str, str]:
    """Every vintage, or with `snapshot` the one real-time view of that date."""
    params = {
        "series_id": series.fred_id,
        "api_key": key,
        "file_type": "json",
        "realtime_start": snapshot or REALTIME_START,
        "realtime_end": snapshot or (SMOKE_END if smoke else REALTIME_END),
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
    fetcher: Fetcher,
    fred: Fred,
    series: FredSeries,
    key: str,
    *,
    smoke: bool,
    snapshot: str | None = None,
) -> list[Download]:
    """Every page of one series, following offsets until `count` rows have arrived."""
    downloads: list[Download] = []
    offset, count = 0, None
    while count is None or offset < count:
        params = _params(series, key, offset, smoke=smoke, snapshot=snapshot)
        result = fetcher.get(fred.api_url, params)
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


def _taken_by_development_end(day: pd.Timestamp) -> bool:
    return bool(pd.notna(day) and day <= pd.Timestamp(DEVELOPMENT_DAY))


def stamp(raw: pd.DataFrame, series: FredSeries) -> pd.DataFrame:
    """Silver rows: actual vintages, approximated before the series' first vintage.

    Rows are approximated only when the first vintage was taken by the development end, so a
    later vintage never dates its values before the holdout.
    """
    actual = end_of_day(raw["realtime_start"], NEW_YORK)
    lag = series.publication_lag_days.value
    approximated = known_after_lag(raw["obs_date"], series.frequency, lag)
    first = raw["realtime_start"].min()
    use = (raw["realtime_start"] == first) & (approximated < actual)
    use &= _taken_by_development_end(first)
    known_at = actual.where(~use, approximated)
    kind = np.where(use, APPROXIMATED, ACTUAL)
    return stamped(series.series_id, raw["obs_date"], known_at, raw["value"], kind)


def stamp_snapshot(raw: pd.DataFrame, series: FredSeries, taken: str) -> pd.DataFrame:
    """Silver rows of one real-time snapshot taken on `taken`.

    A snapshot taken by the development end stamps each row by the publication lag. A later
    snapshot never dates a value before the end of the day it was taken.
    """
    lag = series.publication_lag_days.value
    known_at = known_after_lag(raw["obs_date"], series.frequency, lag)
    day = pd.Timestamp(taken)
    if not _taken_by_development_end(day):
        floor = end_of_day(pd.Series([day]), NEW_YORK).iloc[0]
        known_at = known_at.where(known_at >= floor, floor)
    return stamped(series.series_id, raw["obs_date"], known_at, raw["value"], APPROXIMATED)


def changes(later: pd.DataFrame, base: pd.DataFrame) -> pd.DataFrame:
    """Rows of `later` that are new or differ in value from `base`."""
    previous = base[["obs_date", "value"]].rename(columns={"value": "previous"})
    merged = later.merge(previous, on="obs_date", how="left")
    keep = merged["previous"].isna() | (merged["value"] != merged["previous"])
    return later[keep.to_numpy()].reset_index(drop=True)


def lacks_vintages(error: FetchError, fred: Fred) -> bool:
    """True when FRED's error text says the series has no usable vintage history."""
    text = (error.detail or "").casefold()
    phrases = fred.no_vintage_errors.value
    return error.status == 400 and any(phrase.casefold() in text for phrase in phrases)


def _snapshot(
    fetcher: Fetcher, fred: Fred, series: FredSeries, key: str, *, smoke: bool, taken: str
) -> tuple[list[Download], pd.DataFrame]:
    pages = download(fetcher, fred, series, key, smoke=smoke, snapshot=taken)
    return pages, parse([p.content for p in pages], series.fred_id)


def snapshot_rows(
    fetcher: Fetcher, fred: Fred, series: FredSeries, key: str, *, smoke: bool
) -> tuple[list[Download], pd.DataFrame]:
    """A series without usable vintages, from snapshots instead (decision 0013).

    The snapshot taken at the development end is stamped by the lag. Ingestion adds a snapshot
    taken today, of which only new or changed values are kept, known from today. When FRED
    refuses the development-end snapshot as well, today's snapshot is the only one, also in a
    smoke run, where observations still stop at the development end.
    """
    downloads: list[Download] = []
    frames: list[pd.DataFrame] = []
    base = None
    try:
        pages, base = _snapshot(fetcher, fred, series, key, smoke=smoke, taken=DEVELOPMENT_DAY)
    except FetchError as error:
        if not lacks_vintages(error, fred):
            raise
    else:
        downloads += pages
        frames.append(stamp_snapshot(base, series, DEVELOPMENT_DAY))
        if smoke:
            return downloads, combine(frames)
    taken = today()
    pages, later = _snapshot(fetcher, fred, series, key, smoke=smoke, taken=taken)
    downloads += pages
    frames.append(stamp_snapshot(later if base is None else changes(later, base), series, taken))
    return downloads, combine(frames)


def series_rows(
    fetcher: Fetcher, fred: Fred, series: FredSeries, key: str, *, smoke: bool
) -> tuple[list[Download], pd.DataFrame]:
    """All vintages of a series, or snapshots when FRED says it has no usable vintages."""
    try:
        pages = download(fetcher, fred, series, key, smoke=smoke)
    except FetchError as error:
        if not lacks_vintages(error, fred):
            raise
        return snapshot_rows(fetcher, fred, series, key, smoke=smoke)
    return pages, stamp(parse([p.content for p in pages], series.fred_id), series)


def _probe(fetcher: Fetcher, fred: Fred, item: str, params: dict[str, str]) -> tuple[str, bool]:
    """One vintage request's outcome, and whether it failed with no fallback."""
    try:
        page = _page(fetcher.get(fred.api_url, params).content, item)
    except FetchError as error:
        if lacks_vintages(error, fred):
            return f"failed, snapshot fallback: {error}", False
        return f"failed, no fallback: {error}", True
    except SourceFormatError as error:
        return f"unexpected answer: {error}", True
    return f"ok, {page['count']} rows", False


def inspect(fetcher: Fetcher, sources: Sources) -> Inspection:
    """Per series, the outcome of a vintage request up to the development end and of one over
    the full real-time range; observations stop at the development end in both."""
    key = api_key(sources.fred.api_key_env)
    lines, failures = [], 0
    for series in sources.fred.series:
        to_end = _params(series, key, 0, smoke=True)
        full = {**_params(series, key, 0, smoke=False), "observation_end": SMOKE_END}
        for label, params in (("vintages to " + SMOKE_END, to_end), ("all vintages", full)):
            outcome, failed = _probe(fetcher, sources.fred, series.fred_id, params)
            lines.append(f"{series.fred_id}: {label}: {outcome}")
            failures += failed
    return Inspection(lines, failures)


def collect(fetcher: Fetcher, sources: Sources, *, smoke: bool) -> Batch:
    key = api_key(sources.fred.api_key_env)
    downloads: list[Download] = []
    frames = []
    for series in sources.fred.series:
        pages, frame = series_rows(fetcher, sources.fred, series, key, smoke=smoke)
        frames.append(frame)
        downloads.extend(pages)
    expected = tuple(s.series_id for s in sources.fred.series)
    return Batch(SOURCE, tuple(downloads), combine(frames), expected)


if __name__ == "__main__":
    raise SystemExit(run_cli(SOURCE, collect, inspect=inspect))
