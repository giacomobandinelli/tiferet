"""Robert Shiller's monthly S&P data adapter (plan §5.1; decisions 0013, 0014).

The current `ie_data.xls` link is found on the data page each run rather than stored. The
monthly price is an average of daily closes, so it is known at the end of its month.
Dividends and earnings are interpolated from quarterly totals, so they are known only a
conservative lag after their quarter ends. CPI and the long rate follow the publication lag
of their FRED equivalents. Early years of each series are interpolated from annual figures,
so a month there waits for the end of the following year. The file is a history source: it
is revised in place, and its values are those of the download.

Smoke: `uv run python -m tiferet.data.shiller --smoke`.
"""

import io
import math
from html.parser import HTMLParser
from pathlib import PurePosixPath
from urllib.parse import urljoin, urlsplit

import pandas as pd

from tiferet.data.adapter import Batch, combine, stamped
from tiferet.data.cli import Inspection
from tiferet.data.cli import main as run_cli
from tiferet.data.errors import SourceFormatError
from tiferet.data.fetch import Fetcher, FetchError
from tiferet.data.schema import APPROXIMATED
from tiferet.data.source_config import Fred, Shiller, ShillerRule, ShillerSeries, Sources
from tiferet.data.timing import Frequency, known_after_lag

SOURCE = "shiller"
INSPECT_ROWS = 60


class _Links(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.hrefs: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "a":
            self.hrefs.extend(value for name, value in attrs if name == "href" and value)


def find_link(page: bytes, config: Shiller) -> str:
    """The absolute URL of the one data file named `config.link_name` on the page."""
    parser = _Links()
    parser.feed(page.decode("utf-8", errors="replace"))
    links = {
        urljoin(config.page_url, href)
        for href in parser.hrefs
        if PurePosixPath(urlsplit(href).path).name == config.link_name
    }
    if len(links) != 1:
        raise SourceFormatError(
            SOURCE, f"expected one {config.link_name} link on the page, found {len(links)}"
        )
    link = links.pop()
    if urlsplit(link).scheme != "https":
        raise SourceFormatError(SOURCE, f"the {config.link_name} link is not https")
    return link


def _workbook(content: bytes) -> dict[str, pd.DataFrame]:
    try:
        sheets = pd.read_excel(io.BytesIO(content), sheet_name=None, header=None, engine="calamine")
    except Exception:
        raise SourceFormatError(SOURCE, "the data file is not a readable spreadsheet") from None
    return {str(name): sheet for name, sheet in sheets.items()}


def _sheet(content: bytes, config: Shiller) -> pd.DataFrame:
    sheets = _workbook(content)
    if config.sheet not in sheets:
        raise SourceFormatError(SOURCE, f"the data file has no sheet {config.sheet!r}")
    return sheets[config.sheet]


def _cell(value: object) -> str:
    return str(value).strip() if isinstance(value, str) else ""


def _header(sheet: pd.DataFrame, config: Shiller) -> tuple[int, dict[str, int]]:
    """The header row's position and the column of each configured header."""
    starts = [i for i in range(len(sheet)) if _cell(sheet.iat[i, 0]) == config.date_header]
    if not starts:
        raise SourceFormatError(SOURCE, f"no header row starting with {config.date_header!r}")
    row = [_cell(v) for v in sheet.iloc[starts[0]]]
    wanted = [s.header for s in config.series]
    missing = [h for h in wanted if h not in row]
    if missing:
        raise SourceFormatError(SOURCE, f"the header row lacks {missing}")
    return starts[0], {h: row.index(h) for h in wanted}


def _month(value: object) -> pd.Timestamp | None:
    """Shiller's `YYYY.MM` date (October is written `YYYY.1`), or None for a non-date."""
    if isinstance(value, bool) or not isinstance(value, int | float) or math.isnan(value):
        return None
    year = math.floor(value)
    month = round((value - year) * 100)
    if not 1 <= month <= 12:
        raise SourceFormatError(SOURCE, "a date cell does not hold a month")
    return pd.Timestamp(year=year, month=month, day=1)


def parse(content: bytes, config: Shiller) -> pd.DataFrame:
    """One row per month up to the first non-date row, one column per configured header."""
    sheet = _sheet(content, config)
    start, columns = _header(sheet, config)
    months = []
    for value in sheet.iloc[start + 1 :, 0]:
        month = _month(value)
        if month is None:
            break
        months.append(month)
    if not months:
        raise SourceFormatError(SOURCE, "no monthly rows below the header")
    body = sheet.iloc[start + 1 : start + 1 + len(months)]
    frame = pd.DataFrame({"obs_date": pd.to_datetime(months)})
    for header, position in columns.items():
        frame[header] = pd.to_numeric(body.iloc[:, position], errors="coerce").to_numpy()
    if not frame["obs_date"].is_monotonic_increasing or frame["obs_date"].duplicated().any():
        raise SourceFormatError(SOURCE, "months are not unique and increasing")
    return frame


def _lag(series: ShillerSeries, config: Shiller, fred: Fred) -> tuple[Frequency, int]:
    match series.rule:
        case ShillerRule.MONTH_END:
            return Frequency.MONTHLY, config.price_lag_days.value
        case ShillerRule.QUARTER_END:
            return Frequency.QUARTERLY, config.earnings_dividends_lag_days.value
        case ShillerRule.FOLLOWS:
            assert series.follows is not None
            return Frequency.MONTHLY, fred.by_id(series.follows).publication_lag_days.value


def _known_at(obs: pd.Series, series: ShillerSeries, config: Shiller, fred: Fred) -> pd.Series:
    """The series' rule; before `annual_before`, the end of the following year plus its lag.

    Months before `annual_before` are interpolated between annual figures, so a month can
    depend on the figure of the year after it.
    """
    frequency, lag = _lag(series, config, fred)
    known_at = known_after_lag(obs, frequency, lag)
    if series.annual_before is None:
        return known_at
    early = obs.dt.year < series.annual_before.value
    next_year_end = obs + pd.offsets.YearEnd(0) + pd.DateOffset(years=1)
    return known_at.where(~early, known_after_lag(next_year_end, Frequency.DAILY, lag))


def stamp(wide: pd.DataFrame, config: Shiller, fred: Fred) -> list[pd.DataFrame]:
    frames = []
    for series in config.series:
        rows = wide[["obs_date", series.header]].dropna()
        known_at = _known_at(rows["obs_date"], series, config, fred)
        frames.append(
            stamped(series.series_id, rows["obs_date"], known_at, rows[series.header], APPROXIMATED)
        )
    return frames


def _is_number(value: object) -> bool:
    if isinstance(value, bool):
        return False
    if isinstance(value, int | float):
        return True
    try:
        float(str(value))
    except ValueError:
        return False
    return True


def _header_lines(sheet: pd.DataFrame) -> list[str]:
    """The text cells of each row above the first row of numbers; never a number."""
    lines = []
    for position in range(min(len(sheet), INSPECT_ROWS)):
        row = sheet.iloc[position]
        first = row.iloc[0]
        if _is_number(first) and not (isinstance(first, float) and math.isnan(first)):
            break
        texts = [_cell(v) for v in row]
        texts = [t for t in texts if t and not _is_number(t)]
        if texts:
            lines.append(f"row {position}: " + " | ".join(texts))
    return lines


def inspect(fetcher: Fetcher, sources: Sources) -> Inspection:
    """The data link, the sheet names and the header cells of the data sheet."""
    config = sources.shiller
    lines: list[str] = []
    try:
        link = find_link(fetcher.get(config.page_url).content, config)
        lines.append(f"link: {link}")
        sheets = _workbook(fetcher.get(link).content)
    except (FetchError, SourceFormatError) as error:
        return Inspection([*lines, f"shiller: {error}"], 1)
    lines.append("sheets: " + ", ".join(sheets))
    if config.sheet not in sheets:
        return Inspection([*lines, f"no sheet {config.sheet!r}"], 1)
    header = _header_lines(sheets[config.sheet])
    return Inspection([*lines, f"{config.sheet} header rows:", *header])


def collect(fetcher: Fetcher, sources: Sources, *, smoke: bool) -> Batch:
    config = sources.shiller
    page = fetcher.get(config.page_url)
    data = fetcher.get(find_link(page.content, config))
    frames = stamp(parse(data.content, config), config, sources.fred)
    expected = tuple(s.series_id for s in config.series)
    return Batch(SOURCE, (page, data), combine(frames), expected)


if __name__ == "__main__":
    raise SystemExit(run_cli(SOURCE, collect, inspect=inspect))
