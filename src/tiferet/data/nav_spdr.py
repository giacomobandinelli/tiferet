"""SPDR NAV history adapter for the UCITS holding in US energy (plan §5.1; decisions 0013, 0014,
0016).

The issuer publishes each fund's NAV history as an xlsx workbook at a fixed URL. As the owner
observed (2026-11-02), it holds one sheet, "navhist":

- label and value rows in columns A and B: "Fund Name:", "ISIN:", "Report Date:" and
  "NAV Currency:";
- the header row Date, NAV, Shares Outstanding, Total Net Assets;
- one row per day, newest first: the date as text "dd-Mon-yyyy" with English month
  abbreviations, and numbers in the other columns;
- a footer row whose text starts with "Marketing communication".

The layout is configured as `spdr.layout` in config/sources.yaml and checked strictly: the
ISIN must equal the holding's ISIN in config/holdings.yaml and the NAV currency the configured
one, and an unknown or repeated label, a changed header, a date that does not match, a NAV
that is not a number, a repeated date, a missing footer or any row after the footer is a format
error. Only the NAV series is stored.

Every row is `approximated`: a NAV is known at noon in Berlin on the next TARGET business day
(config/sources.yaml). NAVs are stored in the fund currency.

Smoke: `uv run python -m tiferet.data.nav_spdr --smoke`.
"""

import math
import re
from datetime import date, datetime

import pandas as pd

from tiferet.config import Holdings, load_holdings
from tiferet.data.adapter import Batch, combine, stamped
from tiferet.data.cli import Inspection
from tiferet.data.cli import main as run_cli
from tiferet.data.errors import SourceFormatError
from tiferet.data.excel import read_sheets
from tiferet.data.fetch import Download, Fetcher, FetchError
from tiferet.data.nav import check_dates, header_lines, holding_by_key, known_at_target, positive
from tiferet.data.schema import APPROXIMATED
from tiferet.data.source_config import Sources, Spdr, SpdrFund, SpdrLayout

SOURCE = "spdr"
ENGLISH_MONTHS = {
    name: number
    for number, name in enumerate(
        ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"),
        start=1,
    )
}
"""English month abbreviations, spelled out so that parsing does not depend on the locale."""
_DATE = re.compile(r"(\d{2})-([A-Za-z]{3})-(\d{4})")

Row = list[object]


def workbook(content: bytes) -> dict[str, pd.DataFrame]:
    return read_sheets(content, SOURCE, "NAV file", keep_default_na=False)


def _blank(value: object) -> bool:
    if isinstance(value, float) and math.isnan(value):
        return True
    return value is None or (isinstance(value, str) and not value.strip())


def _empty(row: Row) -> bool:
    return all(_blank(v) for v in row)


def _text(value: object) -> str | None:
    """A cell for `header_lines`: its text, "" when blank, None for a number or a date."""
    if _blank(value):
        return ""
    return value.strip() if isinstance(value, str) else None


def english_date(value: object, item: str) -> pd.Timestamp:
    """The date of a text cell "dd-Mon-yyyy"; any other cell is a format error."""
    if not isinstance(value, str):
        raise SourceFormatError(SOURCE, f"{item}: a cell of the date column is not text")
    match = _DATE.fullmatch(value.strip())
    if match is None:
        raise SourceFormatError(SOURCE, f"{item}: a date does not match dd-Mon-yyyy")
    day, name, year = match.groups()
    month = ENGLISH_MONTHS.get(name)
    if month is None:
        raise SourceFormatError(SOURCE, f"{item}: unknown month abbreviation {name!r}")
    try:
        return pd.Timestamp(year=int(year), month=month, day=int(day))
    except ValueError:
        raise SourceFormatError(SOURCE, f"{item}: a date does not exist") from None


def _label_rows(rows: list[Row], layout: SpdrLayout, item: str) -> tuple[dict[str, object], int]:
    """The value of each label above the header, and the position of the header row.

    A value may be any non-blank cell: only the ISIN and the currency are compared, and the
    report date may be stored as a date.
    """
    header = list(layout.header)
    values: dict[str, object] = {}
    for position, row in enumerate(rows):
        if _empty(row):
            continue
        texts = [v.strip() if isinstance(v, str) else None for v in row if not _blank(v)]
        if texts == header:
            missing = [label for label in layout.labels if label not in values]
            if missing:
                raise SourceFormatError(SOURCE, f"{item}: the file lacks the labels {missing}")
            return values, position
        label = row[0].strip() if isinstance(row[0], str) else None
        if label is None:
            raise SourceFormatError(SOURCE, f"{item}: a row above the header has no text label")
        if label == header[0]:
            raise SourceFormatError(SOURCE, f"{item}: the header row changed")
        if label not in layout.labels:
            raise SourceFormatError(SOURCE, f"{item}: unknown label {label!r} above the header")
        if label in values:
            raise SourceFormatError(SOURCE, f"{item}: the label {label!r} appears twice")
        if _blank(row[1] if len(row) > 1 else None) or not _empty(row[2:]):
            raise SourceFormatError(SOURCE, f"{item}: the label {label!r} has no single value")
        values[label] = row[1]
    raise SourceFormatError(SOURCE, f"{item}: no header row {header}")


def _same_text(value: object, expected: str) -> bool:
    return isinstance(value, str) and value.strip() == expected


def _nav(value: object) -> float | None:
    """The NAV of a number cell; None for anything else, text included."""
    return float(value) if isinstance(value, int | float) and not isinstance(value, bool) else None


def parse(content: bytes, layout: SpdrLayout, fund: SpdrFund, isin: str) -> pd.DataFrame:
    """Rows `obs_date, nav` in ascending date order, after checking the layout strictly."""
    item = fund.holding
    sheets = workbook(content)
    if list(sheets) != [layout.sheet]:
        raise SourceFormatError(
            SOURCE, f"{item}: expected the one sheet {layout.sheet!r}, found {list(sheets)}"
        )
    rows: list[Row] = sheets[layout.sheet].to_numpy(dtype=object).tolist()
    labels, header_at = _label_rows(rows, layout, item)
    if not _same_text(labels[layout.isin_label], isin):
        raise SourceFormatError(
            SOURCE, f"{item}: the ISIN is {labels[layout.isin_label]!r}, expected {isin!r}"
        )
    if not _same_text(labels[layout.currency_label], fund.currency):
        raise SourceFormatError(
            SOURCE,
            f"{item}: the NAV currency is {labels[layout.currency_label]!r}, "
            f"configured {fund.currency!r}",
        )
    cells = [v.strip() if isinstance(v, str) else "" for v in rows[header_at]]
    positions = {name: at for at, name in enumerate(cells) if name}
    date_at, nav_at = positions[layout.date_column], positions[layout.nav_column]
    width = max(positions.values()) + 1
    records: list[tuple[pd.Timestamp, float]] = []
    body = rows[header_at + 1 :]
    for offset, row in enumerate(body):
        if _empty(row):
            continue
        first = row[0].strip() if isinstance(row[0], str) else ""
        if first.startswith(layout.footer_prefix):
            if any(not _empty(later) for later in body[offset + 1 :]):
                raise SourceFormatError(SOURCE, f"{item}: a row follows the footer")
            return check_dates(pd.DataFrame(records, columns=["obs_date", "nav"]), SOURCE, item)
        day = english_date(row[date_at], item)
        nav = _nav(row[nav_at])
        if nav is None:
            raise SourceFormatError(SOURCE, f"{item}: a cell of column 'NAV' is not a number")
        for name, at in positions.items():
            if at in (date_at, nav_at):
                continue
            if _nav(row[at]) is None and not _blank(row[at]):
                raise SourceFormatError(
                    SOURCE, f"{item}: a cell of column {name!r} is not a number"
                )
        if not _empty(row[width:]):
            raise SourceFormatError(SOURCE, f"{item}: a data row holds a cell beyond the header")
        records.append((day, positive(nav, SOURCE, item)))
    raise SourceFormatError(SOURCE, f"{item}: no footer row starting {layout.footer_prefix!r}")


def stamp(raw: pd.DataFrame, config: Spdr, fund: SpdrFund) -> pd.DataFrame:
    known_at = known_at_target(raw["obs_date"], config.publication.value)
    return stamped(fund.series_id, raw["obs_date"], known_at, raw["nav"], APPROXIMATED)


def fund_isin(fund: SpdrFund, holdings: Holdings) -> str:
    """The ISIN of the fund's holding in config/holdings.yaml."""
    isin = holding_by_key(holdings, fund.holding).isin.value
    if isin is None:
        raise ValueError(f"{fund.holding} has no ISIN in config/holdings.yaml")
    return isin


def collect(
    fetcher: Fetcher, sources: Sources, *, smoke: bool, holdings: Holdings | None = None
) -> Batch:
    config = sources.spdr
    known = load_holdings() if holdings is None else holdings
    downloads: list[Download] = []
    frames = []
    for fund in config.funds:
        data = fetcher.get(fund.url)
        raw = parse(data.content, config.layout, fund, fund_isin(fund, known))
        frames.append(stamp(raw, config, fund))
        downloads.append(data)
    expected = tuple(f.series_id for f in config.funds)
    return Batch(SOURCE, tuple(downloads), combine(frames), expected)


def _kind(value: object) -> str:
    if _blank(value):
        return "empty"
    if isinstance(value, str):
        return "text"
    if isinstance(value, datetime | date):
        return "date"
    return "number" if isinstance(value, int | float) else type(value).__name__


def describe(content: bytes, item: str) -> list[str]:
    """The sheet names, and per sheet its header rows and the cell types of its first data
    row; never a value or a date."""
    sheets = workbook(content)
    lines = [f"{item}: sheets: {', '.join(sheets)}"]
    for name, sheet in sheets.items():
        rows = sheet.to_numpy(dtype=object).tolist()
        found, first = header_lines([[_text(v) for v in row] for row in rows])
        lines += [f"{item}: {name} header rows:", *(f"  {line}" for line in found)]
        if first is None:
            lines.append(f"{item}: {name}: no data row found")
        else:
            kinds = ", ".join(_kind(v) for v in rows[first])
            lines.append(f"{item}: {name} first data row cell types: {kinds}")
    return lines


def inspect(fetcher: Fetcher, sources: Sources) -> Inspection:
    lines: list[str] = []
    failures = 0
    for fund in sources.spdr.funds:
        try:
            lines += describe(fetcher.get(fund.url).content, fund.holding)
        except (FetchError, SourceFormatError) as error:
            lines.append(f"{fund.holding}: {error}")
            failures += 1
    return Inspection(lines, failures)


if __name__ == "__main__":
    raise SystemExit(run_cli(SOURCE, collect, inspect=inspect))
