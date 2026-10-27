"""Tests for the bronze manifest (plan §5.2; decision 0013)."""

import hashlib
from pathlib import Path

import pandas as pd
import pandera.errors
import pytest

from tiferet.data.bronze import manifest_rows, read_manifest, record
from tiferet.data.fetch import Download

AT = pd.Timestamp("2022-12-31 12:00", tz="UTC")


def download(url: str, content: bytes = b"abc") -> Download:
    return Download(url, content, AT)


def test_rows_hold_source_url_time_hash_and_size(tmp_path: Path) -> None:
    record("fred", [download("https://x.org/a?api_key=REDACTED&id=1")], tmp_path)
    record("ecb", [download("https://y.org/b", b"")], tmp_path)
    manifest = read_manifest(tmp_path)
    assert list(manifest["source"]) == ["fred", "ecb"]
    assert manifest["sha256"].iloc[0] == hashlib.sha256(b"abc").hexdigest()
    assert list(manifest["bytes"]) == [3, 0]
    assert (manifest["retrieved_at"] == AT).all()


def test_empty_manifest_when_nothing_recorded(tmp_path: Path) -> None:
    assert read_manifest(tmp_path).empty


@pytest.mark.parametrize(
    "url",
    ["https://x.org/a?api_key=secret", "https://x.org/a?api_key=REDACTEDsecret", "http://x.org/a"],
)
def test_unredacted_key_or_plain_http_is_refused(url: str) -> None:
    with pytest.raises((pandera.errors.SchemaError, pandera.errors.SchemaErrors)):
        manifest_rows("fred", [download(url)])
