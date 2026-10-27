"""Fakes and checks shared by the adapter tests."""

from collections.abc import Callable, Mapping
from pathlib import Path

import pandas as pd

from tiferet.data.fetch import Download, build_url, redact
from tiferet.data.pit import Mode, get
from tiferet.data.silver import SilverRoots
from tiferet.data.store import BERLIN, HOLDOUT_START, ParquetStore

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "sources"
RETRIEVED_AT = pd.Timestamp("2022-12-31 12:00", tz="UTC")
RESEARCH_AS_OF = pd.Timestamp("2022-12-31 23:59:59", tz=BERLIN)
PRODUCTION_AS_OF = pd.Timestamp("2023-06-30 23:59:59", tz=BERLIN)

Responder = Callable[[str, dict[str, str]], bytes]


class FakeFetcher:
    """Serves fixture bytes and records each request; redacts URLs like `HttpFetcher`."""

    def __init__(self, responder: Responder) -> None:
        self._responder = responder
        self.calls: list[tuple[str, dict[str, str]]] = []

    def get(self, url: str, params: Mapping[str, str] | None = None) -> Download:
        query = dict(params or {})
        self.calls.append((url, query))
        return Download(redact(build_url(url, query)), self._responder(url, query), RETRIEVED_AT)


def fixture(*parts: str) -> bytes:
    return FIXTURES.joinpath(*parts).read_bytes()


def silver_roots(tmp_path: Path) -> SilverRoots:
    return SilverRoots(tmp_path / "silver", tmp_path / "sealed")


def snapshot(root: Path) -> dict[str, bytes]:
    """Every file under `root` with its bytes."""
    return {
        str(p.relative_to(root)): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()
    }


def assert_partitioned(roots: SilverRoots) -> None:
    """No development file holds a row known at or after the holdout start, and vice versa."""
    for path in roots.development.glob("*.parquet"):
        assert (pd.read_parquet(path)["known_at"] < HOLDOUT_START).all(), path.name
    for path in roots.holdout.glob("*.parquet"):
        assert (pd.read_parquet(path)["known_at"] >= HOLDOUT_START).all(), path.name


def research_view(roots: SilverRoots, series: str) -> pd.DataFrame:
    store = ParquetStore(roots.development, roots.holdout)
    return get(series, RESEARCH_AS_OF, mode=Mode.RESEARCH, store=store, env={})


def production_view(roots: SilverRoots, series: str) -> pd.DataFrame:
    store = ParquetStore(roots.development, roots.holdout)
    return get(series, PRODUCTION_AS_OF, mode=Mode.PRODUCTION, store=store, env={})


def known_at(frame: pd.DataFrame, obs_date: str) -> list[pd.Timestamp]:
    """The `known_at` of every stored version of one observation, in order."""
    rows = frame[frame["obs_date"] == pd.Timestamp(obs_date)].sort_values("known_at")
    return list(rows["known_at"])


def utc(moment: str) -> pd.Timestamp:
    return pd.Timestamp(moment, tz="UTC")
