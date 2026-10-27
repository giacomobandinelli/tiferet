"""Tests for the shared command line: smoke prints counts only; ingest writes (decision 0013)."""

import re
from pathlib import Path

import pytest

from data.adapter_support import FIXTURES, FakeFetcher, fixture
from fixtures.build_sources import FRENCH_ZIPS
from tiferet.data import french
from tiferet.data.bronze import read_manifest
from tiferet.data.cli import main
from tiferet.data.source_config import Sources, load_sources

ZIPS = {name: fixture("french", name) for name in FRENCH_ZIPS}
LINE = re.compile(
    r"^(french: \d+ files, \d+ bytes|[a-z0-9_]+: \d+ rows, \d+ development, \d+ sealed)$"
)


def sources() -> Sources:
    config = load_sources()
    files = tuple(f for f in config.french.files if f.file in ZIPS)
    return config.model_copy(update={"french": config.french.model_copy(update={"files": files})})


def fetcher() -> FakeFetcher:
    return FakeFetcher(lambda url, params: ZIPS[url.rsplit("/", 1)[1]])


def fixture_numbers() -> set[str]:
    """Every number written in the French fixtures, as printed there."""
    text = "".join(p.read_text(encoding="latin-1") for p in (FIXTURES / "french").glob("*.csv"))
    return set(re.findall(r"-?\d+\.\d+", text))


def test_smoke_prints_counts_and_no_values(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    assert main("french", french.collect, ["--smoke"], fetcher=fetcher, sources=sources) == 0
    lines = capsys.readouterr().out.splitlines()
    assert all(LINE.fullmatch(line) for line in lines), lines
    assert "kf_us3_mkt_rf_m: 5 rows, 3 development, 2 sealed" in lines
    assert not any(number in line for line in lines for number in fixture_numbers())
    assert list(tmp_path.iterdir()) == []


def test_ingest_writes_silver_then_manifest(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    roots = ["--silver-root", str(tmp_path / "s"), "--holdout-root", str(tmp_path / "h")]
    argv = ["--ingest", *roots, "--bronze-root", str(tmp_path / "b")]
    assert main("french", french.collect, argv, fetcher=fetcher, sources=sources) == 0
    assert (tmp_path / "s" / "kf_us3_mkt_rf_m.parquet").is_file()
    assert (tmp_path / "h" / "kf_us3_mkt_rf_m.parquet").is_file()
    assert len(read_manifest(tmp_path / "b")) == len(ZIPS)
    assert "kf_us3_mkt_rf_m: 3 development, 2 sealed" in capsys.readouterr().out


def test_an_action_is_required() -> None:
    with pytest.raises(SystemExit):
        main("french", french.collect, [], fetcher=fetcher, sources=sources)
