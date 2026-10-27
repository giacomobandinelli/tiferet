"""Kenneth French Data Library adapter (plan §5.1; decisions 0013, 0014).

Each file is a zipped CSV of titled tables: free-text notes, a title line, a header line that
starts with a comma, and rows keyed by a date of 8 digits (daily), 6 (monthly) or 4
(annual). The configured table is selected by title and date width; -99.99 and -999 mark
missing values; percent returns are stored as decimal fractions, and every 3-factor file
also gives the market return, Mkt-RF + RF.

The library updates monthly and recomputes its full history each time, so it is a history
source, not a live one: a value is known `publication_lag_days` after the end of its month,
and the stored value is the one of the download.

Smoke: `uv run python -m tiferet.data.french --smoke`.
"""

import io
import zipfile
from dataclasses import dataclass, field

import pandas as pd

from tiferet.data.adapter import Batch, combine, stamped
from tiferet.data.cli import main as run_cli
from tiferet.data.errors import SourceFormatError
from tiferet.data.fetch import Download, Fetcher
from tiferet.data.schema import APPROXIMATED
from tiferet.data.source_config import FrenchFile, Sources
from tiferet.data.timing import Frequency, known_after_lag

SOURCE = "french"
MAX_MEMBER_BYTES = 256 * 1024 * 1024
MISSING = (-99.99, -999.0)
DATE_FORMAT = {Frequency.DAILY: "%Y%m%d", Frequency.MONTHLY: "%Y%m"}
DATE_WIDTH = {Frequency.DAILY: 8, Frequency.MONTHLY: 6}
MARKET = "mkt"


@dataclass
class Table:
    title: str
    columns: tuple[str, ...]
    dates: list[str] = field(default_factory=list)
    rows: list[list[str]] = field(default_factory=list)


def read_csv(content: bytes, item: str) -> str:
    """The text of the single CSV member of a library zip."""
    try:
        archive = zipfile.ZipFile(io.BytesIO(content))
    except zipfile.BadZipFile:
        raise SourceFormatError(SOURCE, f"{item}: not a zip archive") from None
    with archive:
        members = [m for m in archive.infolist() if not m.is_dir()]
        if len(members) != 1 or not members[0].filename.lower().endswith(".csv"):
            raise SourceFormatError(SOURCE, f"{item}: expected one CSV member")
        if members[0].file_size > MAX_MEMBER_BYTES:
            raise SourceFormatError(SOURCE, f"{item}: CSV member is larger than the cap")
        return archive.read(members[0]).decode("latin-1")


def tables(text: str) -> list[Table]:
    """Every table in the file, each with the last non-table line above it as its title."""
    found: list[Table] = []
    title, current = "", None
    for raw in text.splitlines():
        line = raw.strip()
        fields = [f.strip() for f in line.split(",")]
        if line.startswith(","):
            current = Table(title, tuple(fields[1:]))
            found.append(current)
        elif current is not None and fields[0].isdigit():
            current.dates.append(fields[0])
            current.rows.append(fields[1:])
        else:
            current = None
            title = " ".join(line.split()) or title
    return found


def select(found: list[Table], file: FrenchFile) -> Table:
    """The configured table: the one with the configured title, else the first table.

    Titles are compared ignoring case and repeated spaces. The table's dates must all have
    the width of the configured frequency.
    """
    if file.section is None:
        if not found:
            raise SourceFormatError(SOURCE, f"{file.file}: no table")
        table = found[0]
    else:
        wanted = " ".join(file.section.split()).lower()
        titled = [t for t in found if t.title.lower() == wanted]
        if len(titled) != 1:
            raise SourceFormatError(
                SOURCE, f"{file.file}: {len(titled)} sections titled {file.section!r}"
            )
        table = titled[0]
    width = DATE_WIDTH[file.frequency]
    if not table.dates or any(len(d) != width for d in table.dates):
        raise SourceFormatError(
            SOURCE, f"{file.file}: table {table.title!r} is not {file.frequency}"
        )
    return table


def _values(table: Table, file: FrenchFile) -> pd.DataFrame:
    if any(len(row) != len(table.columns) for row in table.rows):
        raise SourceFormatError(SOURCE, f"{file.file}: a row's width differs from its header")
    try:
        frame = pd.DataFrame(table.rows, columns=list(table.columns)).astype(float)
    except ValueError:
        raise SourceFormatError(SOURCE, f"{file.file}: a value is not a number") from None
    return frame.mask(frame.isin(MISSING)) / 100


def parse(content: bytes, file: FrenchFile) -> pd.DataFrame:
    """One row per date, one decimal-return column per configured column (and the market)."""
    table = select(tables(read_csv(content, file.file)), file)
    missing = [c for c in file.columns if c not in table.columns]
    if missing:
        raise SourceFormatError(SOURCE, f"{file.file}: section lacks columns {missing}")
    values = _values(table, file)
    try:
        dates = pd.to_datetime(pd.Series(table.dates), format=DATE_FORMAT[file.frequency])
    except ValueError:
        raise SourceFormatError(SOURCE, f"{file.file}: a date is not valid") from None
    if not dates.is_monotonic_increasing or dates.duplicated().any():
        raise SourceFormatError(SOURCE, f"{file.file}: dates are not unique and increasing")
    wide = pd.DataFrame({"obs_date": dates})
    for column in file.columns:
        wide[column] = values[column].to_numpy()
    if file.market_return:
        wide[MARKET] = wide["Mkt-RF"] + wide["RF"]
    return wide


def stamp(wide: pd.DataFrame, file: FrenchFile, lag: int) -> list[pd.DataFrame]:
    """Each value is known `lag` days after the end of the month holding its date."""
    known_at = known_after_lag(wide["obs_date"], Frequency.MONTHLY, lag)
    columns = (*file.columns, MARKET) if file.market_return else file.columns
    frames = []
    for column, series_id in zip(columns, file.series_ids(), strict=True):
        present = wide[column].notna()
        frames.append(
            stamped(
                series_id,
                wide.loc[present, "obs_date"],
                known_at[present],
                wide.loc[present, column],
                APPROXIMATED,
            )
        )
    return frames


def collect(fetcher: Fetcher, sources: Sources, *, smoke: bool) -> Batch:
    config = sources.french
    downloads: list[Download] = []
    frames: list[pd.DataFrame] = []
    for file in config.files:
        result = fetcher.get(config.base_url + file.file)
        frames.extend(stamp(parse(result.content, file), file, config.publication_lag_days.value))
        downloads.append(result)
    expected = tuple(i for f in config.files for i in f.series_ids())
    return Batch(SOURCE, tuple(downloads), combine(frames), expected)


if __name__ == "__main__":
    raise SystemExit(run_cli(SOURCE, collect))
