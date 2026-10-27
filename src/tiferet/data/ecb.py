"""ECB Data Portal adapter (plan §5.1; decisions 0013, 0014).

Series are fetched from the SDMX REST API as SDMX-JSON. Each dataset's values are known at
the ECB's published release time: a number of TARGET business days after the observation, at
a time of day in Europe/Berlin, both from config/sources.yaml. Results built on these series
cite "Source: ECB statistics" and state the calculations derived from them, as the ECB's
reuse policy requires.

Smoke: `uv run python -m tiferet.data.ecb --smoke`.
"""

import json
from typing import Any

import pandas as pd

from tiferet.data.adapter import Batch, combine, stamped
from tiferet.data.cli import main as run_cli
from tiferet.data.errors import SourceFormatError
from tiferet.data.fetch import Download, Fetcher
from tiferet.data.pit import DEVELOPMENT_END
from tiferet.data.schema import APPROXIMATED
from tiferet.data.source_config import EcbDataset, EcbSeries, Sources
from tiferet.data.store import BERLIN
from tiferet.data.timing import at_local_time, shift_target_days

SOURCE = "ecb"
SMOKE_END = DEVELOPMENT_END.date().isoformat()
"""Smoke requests stop here, so they touch no holdout data."""
TIME_PERIOD = "TIME_PERIOD"


def _params(*, smoke: bool) -> dict[str, str]:
    params = {"format": "jsondata", "detail": "dataonly"}
    if smoke:
        params["endPeriod"] = SMOKE_END
    return params


def _get(node: Any, path: tuple[str | int, ...], item: str) -> Any:
    """`node[path[0]][path[1]]...`, or a format error naming the missing element."""
    for step in path:
        try:
            node = node[step]
        except (KeyError, IndexError, TypeError):
            where = ".".join(str(p) for p in path)
            raise SourceFormatError(SOURCE, f"{item}: response lacks {where}") from None
    return node


def _series_key(structure: Any, index: str, item: str) -> str:
    dimensions = _get(structure, ("dimensions", "series"), item)
    positions = index.split(":")
    if not isinstance(dimensions, list) or len(positions) != len(dimensions):
        raise SourceFormatError(SOURCE, f"{item}: series dimensions do not match the key")
    try:
        ids = [str(d["values"][int(p)]["id"]) for d, p in zip(dimensions, positions, strict=True)]
    except (KeyError, IndexError, TypeError, ValueError):
        raise SourceFormatError(SOURCE, f"{item}: series dimensions are malformed") from None
    return ".".join(ids)


def _periods(structure: Any, item: str) -> list[str]:
    dimensions = _get(structure, ("dimensions", "observation"), item)
    matches = [d for d in dimensions if isinstance(d, dict) and d.get("id") == TIME_PERIOD]
    if len(matches) != 1:
        raise SourceFormatError(SOURCE, f"{item}: response has no {TIME_PERIOD} dimension")
    return [str(_get(v, ("id",), item)) for v in _get(matches[0], ("values",), item)]


def parse(content: bytes, flow: str, series: EcbSeries) -> pd.DataFrame:
    """Rows `obs_date, value` of one daily series; missing values dropped."""
    item = f"{flow}.{series.key}"
    try:
        message = json.loads(content)
    except (ValueError, UnicodeDecodeError):
        raise SourceFormatError(SOURCE, f"{item}: response is not JSON") from None
    structure = _get(message, ("structure",), item)
    data_sets = _get(message, ("dataSets",), item)
    if not isinstance(data_sets, list) or len(data_sets) != 1:
        raise SourceFormatError(SOURCE, f"{item}: expected exactly one data set")
    all_series = _get(data_sets[0], ("series",), item)
    if not isinstance(all_series, dict) or len(all_series) != 1:
        raise SourceFormatError(SOURCE, f"{item}: expected exactly one series")
    ((index, body),) = all_series.items()
    if _series_key(structure, index, item) != series.key:
        raise SourceFormatError(SOURCE, f"{item}: response holds a different series")
    periods = _periods(structure, item)
    observations = _get(body, ("observations",), item)
    if not isinstance(observations, dict):
        raise SourceFormatError(SOURCE, f"{item}: observations is not an object")
    rows: list[tuple[str, float]] = []
    for position, observation in observations.items():
        value = _get(observation, (0,), item)
        if value is None:
            continue
        try:
            rows.append((periods[int(position)], float(value)))
        except (IndexError, ValueError, TypeError):
            raise SourceFormatError(SOURCE, f"{item}: malformed observation") from None
    frame = pd.DataFrame(rows, columns=["obs_date", "value"])
    try:
        frame["obs_date"] = pd.to_datetime(frame["obs_date"], format="%Y-%m-%d")
    except ValueError:
        raise SourceFormatError(SOURCE, f"{item}: {TIME_PERIOD} is not a daily date") from None
    if frame["obs_date"].duplicated().any():
        raise SourceFormatError(SOURCE, f"{item}: duplicate {TIME_PERIOD}")
    return frame.sort_values("obs_date").reset_index(drop=True)


def stamp(raw: pd.DataFrame, dataset: EcbDataset, series: EcbSeries) -> pd.DataFrame:
    rule = dataset.publication.value
    release_day = shift_target_days(raw["obs_date"], rule.target_days_after)
    known_at = at_local_time(release_day, rule.time, BERLIN)
    return stamped(series.series_id, raw["obs_date"], known_at, raw["value"], APPROXIMATED)


def collect(fetcher: Fetcher, sources: Sources, *, smoke: bool) -> Batch:
    downloads: list[Download] = []
    frames = []
    for dataset in sources.ecb.datasets:
        for series in dataset.series:
            url = f"{sources.ecb.api_url}/{dataset.flow}/{series.key}"
            result = fetcher.get(url, _params(smoke=smoke))
            raw = parse(result.content, dataset.flow, series)
            frames.append(stamp(raw, dataset, series))
            downloads.append(result)
    expected = tuple(s.series_id for d in sources.ecb.datasets for s in d.series)
    return Batch(SOURCE, tuple(downloads), combine(frames), expected)


if __name__ == "__main__":
    raise SystemExit(run_cli(SOURCE, collect))
