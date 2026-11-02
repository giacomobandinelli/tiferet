"""SPDR NAV history adapter for the UCITS holding in US energy (plan §5.1; decisions 0013, 0014,
0016).

The issuer publishes each fund's NAV history as an xlsx workbook at a fixed URL. The
workbook's layout has not been observed yet: `--inspect` prints its sheet names and header
rows, and the owner records them as `spdr.layout` in config/sources.yaml. Until then
`--smoke` and `--ingest` stop with a format error that says so.

Every row is `approximated`: a NAV is known at noon in Berlin on the next TARGET business day
(config/sources.yaml). NAVs are stored in the fund currency.

Smoke: `uv run python -m tiferet.data.nav_spdr --smoke`.
"""

import math
from datetime import date, datetime

import pandas as pd

from tiferet.data.adapter import Batch, combine, stamped
from tiferet.data.cli import Inspection
from tiferet.data.cli import main as run_cli
from tiferet.data.errors import SourceFormatError
from tiferet.data.excel import read_sheets
from tiferet.data.fetch import Download, Fetcher, FetchError
from tiferet.data.nav import (
    check_dates,
    dated_rows,
    header_lines,
    known_at_target,
    point_number,
    positive,
)
from tiferet.data.schema import APPROXIMATED
from tiferet.data.source_config import Sources, Spdr, SpdrFund, SpdrLayout

SOURCE = "spdr"
NOT_CONFIGURED = (
    "the NAV file layout is not configured yet: run "
    "`uv run python -m tiferet.data.nav_spdr --inspect` and record its sheet name and header "
    "row as spdr.layout in config/sources.yaml"
)


def workbook(content: bytes) -> dict[str, pd.DataFrame]:
    return read_sheets(content, SOURCE, "NAV file", keep_default_na=False)


def _blank(value: object) -> bool:
    if isinstance(value, float) and math.isnan(value):
        return True
    return value is None or (isinstance(value, str) and not value.strip())


def _text(value: object) -> str | None:
    """A cell for `header_lines`: its text, "" when blank, None for a number or a date."""
    if _blank(value):
        return ""
    return value.strip() if isinstance(value, str) else None


def _date(value: object, layout: SpdrLayout, item: str) -> pd.Timestamp | None:
    """The date of a date cell or of text in the layout's format; None for a blank or other
    text. A number in the date column is refused: it may be a date stored as a serial number,
    which the layout does not describe."""
    if isinstance(value, datetime | date):
        moment = pd.Timestamp(value)
        if moment != moment.normalize():
            raise SourceFormatError(SOURCE, f"{item}: a date cell holds a time of day")
        return moment
    if isinstance(value, str):
        try:
            return pd.Timestamp(datetime.strptime(value.strip(), layout.date_format))
        except ValueError:
            return None
    if _blank(value):
        return None
    raise SourceFormatError(SOURCE, f"{item}: a cell of the date column holds a number")


def _number(value: object, item: str) -> float:
    if isinstance(value, int | float) and not isinstance(value, bool):
        return float(value)
    number = point_number(value) if isinstance(value, str) else None
    if number is None:
        raise SourceFormatError(SOURCE, f"{item}: a NAV cell is not a number")
    return number


def parse(content: bytes, layout: SpdrLayout, fund: SpdrFund) -> pd.DataFrame:
    """Rows `obs_date, nav` below the configured header row, in ascending date order."""
    item = fund.holding
    sheets = workbook(content)
    if layout.sheet not in sheets:
        raise SourceFormatError(SOURCE, f"{item}: the NAV file has no sheet {layout.sheet!r}")
    rows = sheets[layout.sheet].to_numpy(dtype=object).tolist()
    header = list(layout.header)
    starts = [
        i
        for i, row in enumerate(rows)
        if [v.strip() for v in row if isinstance(v, str) and v.strip()] == header
    ]
    if not starts:
        raise SourceFormatError(SOURCE, f"{item}: no header row {header}")
    texts = [v.strip() if isinstance(v, str) else None for v in rows[starts[0]]]
    date_at, nav_at = texts.index(layout.date_column), texts.index(layout.nav_column)
    body = rows[starts[0] + 1 :]
    records = [
        (day, positive(_number(row[nav_at], item), SOURCE, item))
        for day, row in dated_rows(
            body, lambda row: _date(row[date_at], layout, item), SOURCE, item
        )
    ]
    return check_dates(pd.DataFrame(records, columns=["obs_date", "nav"]), SOURCE, item)


def stamp(raw: pd.DataFrame, config: Spdr, fund: SpdrFund) -> pd.DataFrame:
    known_at = known_at_target(raw["obs_date"], config.publication.value)
    return stamped(fund.series_id, raw["obs_date"], known_at, raw["nav"], APPROXIMATED)


def collect(fetcher: Fetcher, sources: Sources, *, smoke: bool) -> Batch:
    config = sources.spdr
    if config.layout is None:
        raise SourceFormatError(SOURCE, NOT_CONFIGURED)
    downloads: list[Download] = []
    frames = []
    for fund in config.funds:
        data = fetcher.get(fund.url)
        frames.append(stamp(parse(data.content, config.layout, fund), config, fund))
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
