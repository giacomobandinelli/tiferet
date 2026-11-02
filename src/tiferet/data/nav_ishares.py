"""iShares fund data adapter: daily NAVs of UCITS holdings and US-listed signal ETFs (plan §5.1;
decisions 0013, 0014, 0016).

Each product page, on `ishares.page_host`, links to a fund data file through an absolute URL
whose path holds `/product-data/api/v1/get-fund-document`. The file is served by BlackRock,
which owns iShares, so the link is accepted only on `ishares.data_host` and refused on any
other host. The link is found on the page at every run and never stored. The file is a
SpreadsheetML 2003 workbook, read with the standard library XML parser.

- **UCITS** (German site): sheet "Historical NAVs" holds a title row, then one row of date and
  NAV per day, newest first, then a disclaimer. Dates are German ("26.Okt.2022", "18.Juli2017")
  and NAVs use a decimal comma. The series currency in "Key Facts" must match the configuration.
  The product page of each holding is its `isin` source in config/holdings.yaml.
- **US**: sheet "Historical" holds the header "As Of, NAV per Share, Ex-Dividends, Shares
  Outstanding" and one row per day. The NAV and the ex-dividend amount per share are stored as
  two series; a zero or missing amount is not stored. Total returns of distributing funds are
  computed later from both.

Every row is `approximated`: a UCITS NAV is known at noon in Berlin on the next TARGET business
day, a US NAV at 09:00 New York time on the next US business day (config/sources.yaml).

Smoke: `uv run python -m tiferet.data.nav_ishares --smoke`.
"""

import re
from datetime import datetime
from urllib.parse import parse_qsl, urljoin, urlsplit

import pandas as pd

from tiferet.config import Holdings, load_holdings
from tiferet.data.adapter import Batch, combine, stamped
from tiferet.data.cli import Inspection
from tiferet.data.cli import main as run_cli
from tiferet.data.errors import SourceFormatError
from tiferet.data.fetch import Download, Fetcher, FetchError
from tiferet.data.html_links import page_links
from tiferet.data.nav import (
    check_dates,
    dated_rows,
    header_lines,
    holding_by_key,
    known_at_target,
    known_at_us,
    point_number,
    positive,
)
from tiferet.data.schema import APPROXIMATED
from tiferet.data.source_config import (
    Ishares,
    IsharesUcits,
    IsharesUcitsFund,
    IsharesUs,
    IsharesUsFund,
    Sources,
)
from tiferet.data.spreadsheetml import BLANK, Cell, Sheet, read_workbook

SOURCE = "ishares"
STRING, NUMBER, DATETIME = "String", "Number", "DateTime"

GERMAN_MONTHS = {
    "jan": 1,
    "januar": 1,
    "feb": 2,
    "februar": 2,
    "mär": 3,
    "märz": 3,
    "mrz": 3,
    "apr": 4,
    "april": 4,
    "mai": 5,
    "jun": 6,
    "juni": 6,
    "jul": 7,
    "juli": 7,
    "aug": 8,
    "august": 8,
    "sep": 9,
    "sept": 9,
    "september": 9,
    "okt": 10,
    "oktober": 10,
    "nov": 11,
    "november": 11,
    "dez": 12,
    "dezember": 12,
}
_GERMAN_DATE = re.compile(r"(\d{1,2})\.\s?([^\W\d_]+)\.?\s?(\d{4})")
_GERMAN_NUMBER = re.compile(r"\d{1,3}(?:\.\d{3})+,\d+|\d+(?:,\d+)?")


# Links ------------------------------------------------------------------------------------


def find_link(page: bytes, page_url: str, config: Ishares) -> str:
    """The absolute URL of the one fund data link on the product page."""
    links = {
        urljoin(page_url, href)
        for href in page_links(page)
        if config.link_path in urlsplit(urljoin(page_url, href)).path
    }
    if len(links) != 1:
        raise SourceFormatError(
            SOURCE, f"expected one fund data link on {page_url}, found {len(links)}"
        )
    link = links.pop()
    parts = urlsplit(link)
    if parts.scheme != "https" or parts.netloc.lower().rstrip(".") != config.data_host:
        raise SourceFormatError(
            SOURCE, f"the fund data link on {page_url} is not on https://{config.data_host}"
        )
    names = {name for name, _ in parse_qsl(parts.query, keep_blank_values=True)}
    missing = [name for name in config.link_params if name not in names]
    if missing:
        raise SourceFormatError(SOURCE, f"the fund data link on {page_url} lacks {missing}")
    return link


def ucits_page(fund: IsharesUcitsFund, holdings: Holdings, config: Ishares) -> str:
    """The product page of a UCITS holding: the source of its ISIN in config/holdings.yaml,
    which must be on the configured page host."""
    url = holding_by_key(holdings, fund.holding).isin.source_url
    if urlsplit(url).netloc != config.page_host:
        raise ValueError(f"the ISIN source of {fund.holding} is not on {config.page_host}")
    return url


# Cells ------------------------------------------------------------------------------------


def _filled(row: list[Cell]) -> list[Cell]:
    return [cell for cell in row if cell.text]


def _datetime_cell(cell: Cell, item: str) -> pd.Timestamp:
    try:
        moment = pd.Timestamp(datetime.fromisoformat(cell.text))
    except ValueError:
        raise SourceFormatError(SOURCE, f"{item}: a date cell is malformed") from None
    if moment != moment.normalize():
        raise SourceFormatError(SOURCE, f"{item}: a date cell holds a time of day")
    return moment


def german_date(cell: Cell, item: str) -> pd.Timestamp | None:
    """The date of a German date cell, or None for a cell that is not a date.

    A cell shaped like a date with an unknown month name or an impossible day is an error.
    """
    if cell.kind == DATETIME:
        return _datetime_cell(cell, item)
    if cell.kind != STRING:
        return None
    match = _GERMAN_DATE.fullmatch(cell.text)
    if match is None:
        return None
    day, name, year = match.groups()
    month = GERMAN_MONTHS.get(name.lower())
    if month is None:
        raise SourceFormatError(SOURCE, f"{item}: unknown month name {name!r}")
    try:
        return pd.Timestamp(year=int(year), month=month, day=int(day))
    except ValueError:
        raise SourceFormatError(SOURCE, f"{item}: a date does not exist") from None


def german_number(cell: Cell, item: str) -> float:
    """A number cell, or text with a decimal comma and optional thousands dots."""
    if cell.kind == NUMBER:
        try:
            return float(cell.text)
        except ValueError:
            raise SourceFormatError(SOURCE, f"{item}: a number cell is malformed") from None
    if cell.kind == STRING and _GERMAN_NUMBER.fullmatch(cell.text):
        return float(cell.text.replace(".", "").replace(",", "."))
    raise SourceFormatError(SOURCE, f"{item}: a NAV cell is not a number")


def us_date(cell: Cell, date_format: str, item: str) -> pd.Timestamp | None:
    """The date of a date cell or of text in `date_format`, or None for any other cell."""
    if cell.kind == DATETIME:
        return _datetime_cell(cell, item)
    if cell.kind != STRING:
        return None
    try:
        return pd.Timestamp(datetime.strptime(cell.text, date_format))
    except ValueError:
        return None


def us_number(cell: Cell, item: str, what: str) -> float:
    """A number cell, or text with a decimal point and optional thousands commas."""
    if cell.kind == NUMBER:
        try:
            return float(cell.text)
        except ValueError:
            raise SourceFormatError(SOURCE, f"{item}: a number cell is malformed") from None
    number = point_number(cell.text) if cell.kind == STRING else None
    if number is not None:
        return number
    raise SourceFormatError(SOURCE, f"{item}: {what} is not a number")


# Parsers ----------------------------------------------------------------------------------


def _sheet(sheets: dict[str, Sheet], name: str, item: str) -> Sheet:
    if name not in sheets:
        raise SourceFormatError(SOURCE, f"{item}: the fund data file has no sheet {name!r}")
    return sheets[name]


def series_currency(sheets: dict[str, Sheet], config: IsharesUcits, item: str) -> str:
    """The value next to `currency_label` in the key facts sheet."""
    rows = [
        _filled(row)
        for row in _sheet(sheets, config.key_facts_sheet, item)
        if _filled(row) and _filled(row)[0].text == config.currency_label
    ]
    if len(rows) != 1 or len(rows[0]) != 2:
        raise SourceFormatError(
            SOURCE, f"{item}: expected one {config.currency_label!r} row with one value"
        )
    return rows[0][1].text


def parse_ucits(content: bytes, config: IsharesUcits, fund: IsharesUcitsFund) -> pd.DataFrame:
    """Rows `obs_date, nav` of a UCITS fund data file, in ascending date order."""
    item = fund.holding
    sheets = read_workbook(content, SOURCE)
    currency = series_currency(sheets, config, item)
    if currency != fund.currency:
        raise SourceFormatError(
            SOURCE, f"{item}: the series currency is {currency!r}, configured {fund.currency!r}"
        )
    rows = [row for row in _sheet(sheets, config.nav_sheet, item) if _filled(row)]
    if not rows or [c.text for c in _filled(rows[0])] != [config.nav_title]:
        raise SourceFormatError(
            SOURCE, f"{item}: {config.nav_sheet!r} does not start with {config.nav_title!r}"
        )
    records = []
    for day, row in dated_rows(
        rows[1:], lambda row: german_date(_filled(row)[0], item), SOURCE, item
    ):
        cells = _filled(row)
        if len(cells) != 2:
            raise SourceFormatError(SOURCE, f"{item}: a NAV row does not hold a date and a NAV")
        records.append((day, positive(german_number(cells[1], item), SOURCE, item)))
    frame = pd.DataFrame(records, columns=["obs_date", "nav"])
    return check_dates(frame, SOURCE, item)


def _header_row(sheet: Sheet, header: tuple[str, ...]) -> int | None:
    for position, row in enumerate(sheet):
        if tuple(c.text for c in _filled(row)) == header:
            return position
    return None


def _distribution(cell: Cell, config: IsharesUs, item: str) -> float:
    """The ex-dividend amount per share; NaN for an empty or missing-marker cell."""
    if not cell.text or (cell.kind == STRING and cell.text in config.missing):
        return float("nan")
    amount = us_number(cell, item, "an ex-dividend amount")
    if amount < 0:
        raise SourceFormatError(SOURCE, f"{item}: an ex-dividend amount is negative")
    return amount


def parse_us(content: bytes, config: IsharesUs, fund: IsharesUsFund) -> pd.DataFrame:
    """Rows `obs_date, nav, dist` of a US fund data file; `dist` is NaN when missing."""
    item = fund.ticker
    sheet = _sheet(read_workbook(content, SOURCE), config.sheet, item)
    start = _header_row(sheet, config.header)
    if start is None:
        raise SourceFormatError(
            SOURCE, f"{item}: {config.sheet!r} has no header row {list(config.header)}"
        )

    def to_date(row: list[Cell]) -> pd.Timestamp | None:
        return us_date(row[0], config.date_format, item) if row else None

    records = []
    for day, row in dated_rows(sheet[start + 1 :], to_date, SOURCE, item):
        nav = positive(us_number(_at(row, 1), item, "a NAV"), SOURCE, item)
        records.append((day, nav, _distribution(_at(row, 2), config, item)))
    frame = pd.DataFrame(records, columns=["obs_date", "nav", "dist"])
    frame = check_dates(frame, SOURCE, item)
    if not fund.distributions and (frame["dist"] > 0).any():
        raise SourceFormatError(
            SOURCE, f"{item}: ex-dividend amounts found for a fund configured without them"
        )
    return frame


def _at(row: list[Cell], position: int) -> Cell:
    return row[position] if len(row) > position else BLANK


# Adapter ----------------------------------------------------------------------------------


def stamp_ucits(raw: pd.DataFrame, config: IsharesUcits, fund: IsharesUcitsFund) -> pd.DataFrame:
    known_at = known_at_target(raw["obs_date"], config.publication.value)
    return stamped(fund.series_id, raw["obs_date"], known_at, raw["nav"], APPROXIMATED)


def stamp_us(raw: pd.DataFrame, config: IsharesUs, fund: IsharesUsFund) -> list[pd.DataFrame]:
    rule = config.publication.value
    paid = raw[raw["dist"] > 0]
    frames = [
        stamped(
            fund.nav_series_id,
            raw["obs_date"],
            known_at_us(raw["obs_date"], rule),
            raw["nav"],
            APPROXIMATED,
        ),
        stamped(
            fund.dist_series_id,
            paid["obs_date"],
            known_at_us(paid["obs_date"], rule),
            paid["dist"],
            APPROXIMATED,
        ),
    ]
    return frames if fund.distributions else frames[:1]


def expected_series(config: Ishares) -> tuple[str, ...]:
    return (
        *(f.series_id for f in config.ucits.funds),
        *(i for f in config.us.funds for i in f.series_ids()),
    )


def collect(
    fetcher: Fetcher, sources: Sources, *, smoke: bool, holdings: Holdings | None = None
) -> Batch:
    config = sources.ishares
    known = load_holdings() if holdings is None else holdings
    downloads: list[Download] = []
    frames: list[pd.DataFrame] = []
    for fund in config.ucits.funds:
        page_url = ucits_page(fund, known, config)
        page = fetcher.get(page_url)
        data = fetcher.get(find_link(page.content, page_url, config))
        frames.append(
            stamp_ucits(parse_ucits(data.content, config.ucits, fund), config.ucits, fund)
        )
        downloads += [page, data]
    for us_fund in config.us.funds:
        page = fetcher.get(us_fund.page_url)
        data = fetcher.get(find_link(page.content, us_fund.page_url, config))
        frames += stamp_us(parse_us(data.content, config.us, us_fund), config.us, us_fund)
        downloads += [page, data]
    return Batch(SOURCE, tuple(downloads), combine(frames), expected_series(config))


# Inspect ----------------------------------------------------------------------------------


def _cell_text(cell: Cell) -> str | None:
    """A cell for `header_lines`: its text, or None for a number or a date."""
    return None if cell.kind in (NUMBER, DATETIME) else cell.text


def _describe_link(link: str) -> str:
    parts = urlsplit(link)
    names = [name for name, _ in parse_qsl(parts.query, keep_blank_values=True)]
    return f"link {parts.scheme}://{parts.netloc}{parts.path} with parameters {', '.join(names)}"


def _describe_sheet(sheets: dict[str, Sheet], name: str, item: str) -> list[str]:
    if name not in sheets:
        return [f"{item}: no sheet {name!r}"]
    rows = sheets[name]
    lines, first = header_lines([[_cell_text(c) for c in row] for row in rows])
    out = [f"{item}: {name} header rows:", *(f"  {line}" for line in lines)]
    if first is None:
        out.append(f"{item}: {name}: no data row found")
    else:
        kinds = ", ".join(c.kind or "empty" for c in rows[first])
        out.append(f"{item}: {name} first data row cell types: {kinds}")
    return out


def _inspect_fund(
    fetcher: Fetcher, page_url: str, item: str, config: Ishares, sheet_names: list[str]
) -> tuple[list[str], dict[str, Sheet]]:
    link = find_link(fetcher.get(page_url).content, page_url, config)
    sheets = read_workbook(fetcher.get(link).content, SOURCE)
    lines = [f"{item}: {_describe_link(link)}", f"{item}: sheets: {', '.join(sheets)}"]
    for name in sheet_names:
        lines += _describe_sheet(sheets, name, item)
    return lines, sheets


def inspect(fetcher: Fetcher, sources: Sources, holdings: Holdings | None = None) -> Inspection:
    """Per fund: the link without its values, the sheet names, the header rows of the NAV
    sheet and the cell types of its first data row; for UCITS funds, the series currency."""
    config = sources.ishares
    known = load_holdings() if holdings is None else holdings
    lines: list[str] = []
    failures = 0
    for fund in config.ucits.funds:
        try:
            page = ucits_page(fund, known, config)
            found, sheets = _inspect_fund(
                fetcher, page, fund.holding, config, [config.ucits.nav_sheet]
            )
            lines += found
            currency = series_currency(sheets, config.ucits, fund.holding)
            lines.append(f"{fund.holding}: {config.ucits.currency_label}: {currency}")
        except (FetchError, SourceFormatError) as error:
            lines.append(f"{fund.holding}: {error}")
            failures += 1
    for us_fund in config.us.funds:
        try:
            found, _ = _inspect_fund(
                fetcher, us_fund.page_url, us_fund.ticker, config, [config.us.sheet]
            )
            lines += found
        except (FetchError, SourceFormatError) as error:
            lines.append(f"{us_fund.ticker}: {error}")
            failures += 1
    return Inspection(lines, failures)


if __name__ == "__main__":
    raise SystemExit(run_cli(SOURCE, collect, inspect=inspect))
