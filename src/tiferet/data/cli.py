"""Command line shared by the adapters: `python -m tiferet.data.<source> --smoke | --ingest |
--inspect`.

`--smoke` downloads every configured item, parses and stamps it, and prints counts only:
rows per series and how many would go to each partition. It writes nothing and prints no
values. `--ingest` writes the silver tables and then appends the bronze manifest. Both run
in production mode and are run by the owner only; agents never download live data. API keys
are read from the process environment only, for example with `uv run --env-file .env`; no
`.env` file is loaded into the process.

`--inspect` downloads each configured item and prints its structure only (section titles,
column headers, sheet names, dimension ids, request outcomes and counts), never a value or an
observation date, so the configuration can be corrected from the real files. It writes
nothing, and exits with status 1 when any item could not be inspected.
"""

import argparse
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from tiferet.data import bronze
from tiferet.data.adapter import Collect, ingest, smoke_lines
from tiferet.data.fetch import Fetcher, HttpFetcher
from tiferet.data.silver import SilverRoots
from tiferet.data.source_config import Sources, load_sources
from tiferet.data.store import HOLDOUT_SILVER_DIR, SILVER_DIR


@dataclass(frozen=True)
class Inspection:
    """Structure lines of an inspect run and how many items could not be inspected."""

    lines: list[str]
    failures: int = 0


class Inspect(Protocol):
    """Signature of an adapter's `inspect(fetcher, sources)`: structure lines, no values."""

    def __call__(self, fetcher: Fetcher, sources: Sources) -> Inspection: ...


def _parser(source: str) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog=f"python -m tiferet.data.{source}")
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--smoke", action="store_true", help="download and check; write nothing")
    action.add_argument("--ingest", action="store_true", help="write silver and the manifest")
    action.add_argument(
        "--inspect", action="store_true", help="print each item's structure; no values"
    )
    parser.add_argument("--silver-root", type=Path, default=SILVER_DIR)
    parser.add_argument("--holdout-root", type=Path, default=HOLDOUT_SILVER_DIR)
    parser.add_argument("--bronze-root", type=Path, default=bronze.BRONZE_DIR)
    return parser


def main(
    source: str,
    collect: Collect,
    argv: Sequence[str] | None = None,
    *,
    inspect: Inspect | None = None,
    fetcher: Callable[[], Fetcher] = HttpFetcher,
    sources: Callable[[], Sources] = load_sources,
) -> int:
    args = _parser(source).parse_args(argv)
    config = sources()
    if args.inspect:
        if inspect is None:
            raise SystemExit(f"{source} has no inspect mode")
        inspection = inspect(fetcher(), config)
        print("\n".join(inspection.lines))
        return 1 if inspection.failures else 0
    batch = collect(fetcher(), config, smoke=args.smoke)
    if args.smoke:
        print("\n".join(smoke_lines(batch)))
        return 0
    roots = SilverRoots(args.silver_root, args.holdout_root)
    max_loss = float(config.silver.max_row_loss_fraction.value)
    for item in ingest(batch, roots, args.bronze_root, max_loss):
        print(f"{item.series_id}: {item.development} development, {item.holdout} sealed")
    return 0
