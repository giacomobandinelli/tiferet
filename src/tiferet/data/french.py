"""Kenneth French Data Library adapter (plan §5.1; decisions 0013, 0014).

Each file is a zipped CSV of tables: free-text notes, usually a title line, a header line
that normally starts with a comma, and rows keyed by a date of 8 digits (daily), 6 (monthly)
or 4 (annual). A portfolio table is selected by its normalised title and its date width; a
3-factor table, which has no title, as the first table of the date width whose header holds
the configured factors. Column headers are matched by their series word, so the monthly
size file's "2-Dec" and the daily file's "Dec 2" are one series. -99.99 and -999 mark
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
from tiferet.data.cli import Inspection
from tiferet.data.cli import main as run_cli
from tiferet.data.errors import SourceFormatError
from tiferet.data.fetch import Download, Fetcher, FetchError
from tiferet.data.schema import APPROXIMATED
from tiferet.data.source_config import FrenchFile, Sources, column_key, title_key
from tiferet.data.timing import Frequency, known_after_lag

SOURCE = "french"
MAX_MEMBER_BYTES = 256 * 1024 * 1024
MISSING = (-99.99, -999.0)
DATE_FORMAT = {Frequency.DAILY: "%Y%m%d", Frequency.MONTHLY: "%Y%m"}
DATE_WIDTH = {Frequency.DAILY: 8, Frequency.MONTHLY: 6}
MARKET = "mkt"


ABOVE_LINES = 3
"""Non-numeric lines kept above a table that has no header line, for `--inspect`."""
MAX_LINE = 160


@dataclass
class Table:
    """A run of data rows with the title and header found above it.

    `columns` is None when no header line was recognised; `above` then holds the last
    non-numeric lines before the first row, so the layout can be reported.
    """

    title: str
    columns: tuple[str, ...] | None
    above: tuple[str, ...] = ()
    dates: list[str] = field(default_factory=list)
    rows: list[list[str]] = field(default_factory=list)

    @property
    def date_width(self) -> int:
        return len(self.dates[0])


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


def _is_row(fields: list[str]) -> bool:
    return len(fields) > 1 and fields[0].isdigit()


def _header(line: str, width: int) -> tuple[str, ...] | None:
    """The column names of a header line above rows of `width` fields, or None.

    A header normally starts with a comma. One without it is accepted when its fields are
    exactly the row's value columns. Whitespace around fields is ignored.
    """
    fields = [f.strip() for f in line.split(",")]
    if line.startswith(","):
        columns = fields[1:]
    elif len(fields) == width - 1:
        columns = fields
    else:
        return None
    return tuple(columns) if all(columns) else None


def tables(text: str) -> list[Table]:
    """Every table in the file.

    The header is the non-blank line directly above the first row, if it has one; the title
    is the line above the header, or empty. The 3-factor files have a note there instead.
    """
    found: list[Table] = []
    above: list[str] = []
    current: Table | None = None
    for raw in text.splitlines():
        line = raw.strip()
        fields = [f.strip() for f in line.split(",")]
        if _is_row(fields):
            if current is None:
                columns = _header(above[-1], len(fields)) if above else None
                if columns is None:
                    title = above[-1] if above else ""
                    shown = tuple(line[:MAX_LINE] for line in above[-ABOVE_LINES:])
                else:
                    title, shown = (above[-2] if len(above) > 1 else ""), ()
                current = Table(title, columns, shown)
                found.append(current)
            current.dates.append(fields[0])
            current.rows.append(fields[1:])
        else:
            if current is not None:
                current, above = None, []
            if line and not fields[0].isdigit():
                above.append(" ".join(line.split()))
    return found


def _keys(table: Table, file: FrenchFile) -> list[str]:
    """The series word of each column of `table`; unknown or repeated headers are refused."""
    assert table.columns is not None
    keys = [column_key(c) for c in table.columns]
    unknown = [c for c, k in zip(table.columns, keys, strict=True) if k is None]
    if unknown:
        raise SourceFormatError(SOURCE, f"{file.file}: unknown column headers {unknown}")
    known = [k for k in keys if k is not None]
    repeated = sorted({k for k in known if known.count(k) > 1})
    if repeated:
        raise SourceFormatError(SOURCE, f"{file.file}: column headers repeat {repeated}")
    return known


def _has_columns(table: Table, file: FrenchFile) -> bool:
    if table.columns is None:
        return False
    keys = {column_key(c) for c in table.columns}
    return {column_key(c) for c in file.columns} <= keys


def _no_factor_table(sized: list[Table], file: FrenchFile) -> str:
    """Why no table of the file's date width holds every configured column."""
    wanted = {column_key(c) for c in file.columns}
    headed = [t for t in sized if t.columns is not None]
    for table in headed:
        assert table.columns is not None
        present = {column_key(c) for c in table.columns}
        if present & wanted:
            lacking = [c for c in file.columns if column_key(c) not in present]
            return (
                f"the {file.frequency} table lacks configured columns {lacking}; "
                f"its header is {', '.join(table.columns)}"
            )
    unlabelled = len(sized) - len(headed)
    return (
        f"no table with {file.frequency} dates and columns {list(file.columns)}; "
        f"{unlabelled} tables of that date width have no header line"
    )


def select(found: list[Table], file: FrenchFile) -> Table:
    """The configured table.

    A portfolio file names its table by `section`: the table must have that title once
    normalised (case, spaces, "Weight"/"Weighted", a leading "Average") and the date width
    of the file's frequency, and exactly one must. A 3-factor file has no title: its table
    is the first with the date width and a header holding every configured column. All the
    table's dates must then have that width.
    """
    width = DATE_WIDTH[file.frequency]
    sized = [t for t in found if t.date_width == width]
    if file.section is None:
        matches = [t for t in sized if _has_columns(t, file)]
        if not matches:
            raise SourceFormatError(SOURCE, f"{file.file}: {_no_factor_table(sized, file)}")
        table = matches[0]
    else:
        wanted = title_key(file.section)
        matches = [t for t in sized if title_key(t.title) == wanted]
        if len(matches) != 1:
            raise SourceFormatError(
                SOURCE,
                f"{file.file}: {len(matches)} tables titled {file.section!r} "
                f"with {file.frequency} dates",
            )
        table = matches[0]
    if any(len(d) != width for d in table.dates):
        raise SourceFormatError(
            SOURCE, f"{file.file}: table {table.title!r} is not {file.frequency}"
        )
    if table.columns is None:
        raise SourceFormatError(SOURCE, f"{file.file}: table {table.title!r} has no header line")
    return table


def _values(table: Table, file: FrenchFile) -> pd.DataFrame:
    keys = _keys(table, file)
    if any(len(row) != len(keys) for row in table.rows):
        raise SourceFormatError(SOURCE, f"{file.file}: a row's width differs from its header")
    try:
        frame = pd.DataFrame(table.rows, columns=keys).astype(float)
    except ValueError:
        raise SourceFormatError(SOURCE, f"{file.file}: a value is not a number") from None
    return frame.mask(frame.isin(MISSING)) / 100


def parse(content: bytes, file: FrenchFile) -> pd.DataFrame:
    """One row per date, one decimal-return column per configured column (and the market)."""
    table = select(tables(read_csv(content, file.file)), file)
    values = _values(table, file)
    missing = [c for c in file.columns if column_key(c) not in values.columns]
    if missing:
        raise SourceFormatError(SOURCE, f"{file.file}: table lacks columns {missing}")
    try:
        dates = pd.to_datetime(pd.Series(table.dates), format=DATE_FORMAT[file.frequency])
    except ValueError:
        raise SourceFormatError(SOURCE, f"{file.file}: a date is not valid") from None
    if not dates.is_monotonic_increasing or dates.duplicated().any():
        raise SourceFormatError(SOURCE, f"{file.file}: dates are not unique and increasing")
    wide = pd.DataFrame({"obs_date": dates})
    for column in file.columns:
        wide[column] = values[column_key(column)].to_numpy()
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


def describe(found: list[Table]) -> list[str]:
    """Each table's title, column header and row count; never a value or a date.

    A table with no recognised header line gets the non-numeric lines directly above its
    first row instead, so the layout can be reported and the parser corrected.
    """
    lines = []
    for table in found:
        widths = sorted({len(d) for d in table.dates})
        lines += [f"  title: {table.title}"]
        if table.columns is None:
            lines += ["  header: none found; lines above the first row:"]
            lines += [f"    | {text}" for text in table.above]
        else:
            lines += [f"  header: {', '.join(table.columns)}"]
        lines += [f"  rows: {len(table.dates)}, date widths {widths}"]
    return lines


def inspect(fetcher: Fetcher, sources: Sources) -> Inspection:
    """Every table title, column header (or the lines above a missing one) of every file."""
    lines, failures = [], 0
    for file in sources.french.files:
        try:
            text = read_csv(fetcher.get(sources.french.base_url + file.file).content, file.file)
        except (FetchError, SourceFormatError) as error:
            lines.append(f"{file.file}: {error}")
            failures += 1
            continue
        found = tables(text)
        lines += [f"{file.file}: {len(found)} tables", *describe(found)]
    return Inspection(lines, failures)


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
    raise SystemExit(run_cli(SOURCE, collect, inspect=inspect))
