"""Bronze manifest: one row per downloaded file (plan §5.2; decision 0013).

The manifest records where each file came from, when, and its SHA-256 and size. Raw files
are not kept: every source in this layer can be downloaded again, and the hash shows when a
history source has recomputed its past values.
"""

import hashlib
from collections.abc import Sequence
from pathlib import Path

import pandas as pd
import pandera.pandas as pa

from tiferet.data.fetch import Download
from tiferet.data.silver import write_parquet_atomic
from tiferet.data.store import DATA_DIR

BRONZE_DIR = DATA_DIR / "bronze"
MANIFEST_FILE = "manifest.parquet"

SOURCE, URL, RETRIEVED_AT, SHA256, BYTES = "source", "url", "retrieved_at", "sha256", "bytes"
UNREDACTED_KEY = r"api_key=(?!REDACTED(?:&|$))"

MANIFEST_SCHEMA = pa.DataFrameSchema(
    {
        SOURCE: pa.Column(str, pa.Check.str_matches(r"^[a-z0-9_]+$")),
        URL: pa.Column(
            str,
            [
                pa.Check.str_matches(r"^https://"),
                pa.Check(lambda s: ~s.str.contains(UNREDACTED_KEY), error="unredacted key"),
            ],
        ),
        RETRIEVED_AT: pa.Column(pd.DatetimeTZDtype("us", "UTC")),
        SHA256: pa.Column(str, pa.Check.str_matches(r"^[0-9a-f]{64}$")),
        BYTES: pa.Column("int64", pa.Check.ge(0)),
    },
    strict=True,
)


def manifest_rows(source: str, downloads: Sequence[Download]) -> pd.DataFrame:
    """Manifest rows for `downloads`, validated."""
    frame = pd.DataFrame(
        {
            SOURCE: pd.Series([source] * len(downloads), dtype=str),
            URL: pd.Series([d.url for d in downloads], dtype=str),
            RETRIEVED_AT: pd.Series(
                [d.retrieved_at for d in downloads], dtype="datetime64[us, UTC]"
            ),
            SHA256: pd.Series(
                [hashlib.sha256(d.content).hexdigest() for d in downloads], dtype=str
            ),
            BYTES: pd.Series([len(d.content) for d in downloads], dtype="int64"),
        }
    )
    validated: pd.DataFrame = MANIFEST_SCHEMA.validate(frame)
    return validated


def read_manifest(root: Path) -> pd.DataFrame:
    """The manifest under `root`; empty when nothing has been recorded."""
    path = root / MANIFEST_FILE
    if not path.is_file():
        return manifest_rows("none", [])
    validated: pd.DataFrame = MANIFEST_SCHEMA.validate(pd.read_parquet(path))
    return validated


def record(source: str, downloads: Sequence[Download], root: Path) -> None:
    """Append one manifest row per download."""
    rows = manifest_rows(source, downloads)
    combined = pd.concat([read_manifest(root), rows], ignore_index=True)
    validated: pd.DataFrame = MANIFEST_SCHEMA.validate(combined)
    write_parquet_atomic(validated, root / MANIFEST_FILE)
