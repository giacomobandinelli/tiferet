"""Pieces shared by the issuer NAV adapters (plan §5.1; decisions 0013, 0016).

A NAV for day t is struck after that day's valuation point and published later. Its
`known_at` follows a conservative rule from config/sources.yaml: a number of business days
after t, at a local time. UCITS funds count TARGET business days in Europe/Berlin; US-listed
funds count US business days in America/New York. NAVs are stored in the currency of the
series; conversion to EUR happens later with ECB reference rates.

Issuer data is licensed for personal or internal use only, so raw NAVs are never published
(decision 0016), and `--inspect` prints structure only: no cell with a digit is ever shown.
"""

import math
import re
from collections.abc import Callable, Sequence

import pandas as pd

from tiferet.config import Holding, Holdings
from tiferet.data.errors import SourceFormatError
from tiferet.data.source_config import NavPublication
from tiferet.data.store import BERLIN
from tiferet.data.timing import NEW_YORK, at_local_time, shift_target_days, shift_us_days

INSPECT_ROWS = 40
"""Rows of a sheet `header_lines` looks at."""
_DIGIT = re.compile(r"\d")
_POINT_NUMBER = re.compile(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?")


def holding_by_key(holdings: Holdings, key: str) -> Holding:
    """The one holding with this key in config/holdings.yaml."""
    matches = [h for h in holdings.holdings if h.key == key]
    if len(matches) != 1:
        raise ValueError(f"{key} is not a holding in config/holdings.yaml")
    return matches[0]


def known_at_target(obs: pd.Series, rule: NavPublication) -> pd.Series:
    """`rule.time` in Berlin, `rule.business_days_after` TARGET business days after each day."""
    return at_local_time(shift_target_days(obs, rule.business_days_after), rule.time, BERLIN)


def known_at_us(obs: pd.Series, rule: NavPublication) -> pd.Series:
    """`rule.time` in New York, `rule.business_days_after` US business days after each day."""
    return at_local_time(shift_us_days(obs, rule.business_days_after), rule.time, NEW_YORK)


def positive(value: float, source: str, item: str) -> float:
    """`value` if it is a finite positive number, else a format error."""
    if not math.isfinite(value) or value <= 0:
        raise SourceFormatError(source, f"{item}: a NAV is not a positive number")
    return value


def point_number(text: str) -> float | None:
    """Text with a decimal point and optional thousands commas as a number, else None."""
    text = text.strip()
    return float(text.replace(",", "")) if _POINT_NUMBER.fullmatch(text) else None


def dated_rows[R](
    rows: Sequence[R], to_date: Callable[[R], pd.Timestamp | None], source: str, item: str
) -> list[tuple[pd.Timestamp, R]]:
    """The leading rows that `to_date` dates, with their dates.

    The NAV rows end at the first undated row; a dated row after it is a format error, so a
    note between two blocks of rows never hides the second block.
    """
    found: list[tuple[pd.Timestamp, R]] = []
    for position, row in enumerate(rows):
        day = to_date(row)
        if day is None:
            if any(to_date(later) is not None for later in rows[position + 1 :]):
                raise SourceFormatError(
                    source, f"{item}: a dated row follows the end of the NAV rows"
                )
            break
        found.append((day, row))
    return found


def check_dates(frame: pd.DataFrame, source: str, item: str) -> pd.DataFrame:
    """`frame` sorted by `obs_date`, which must be unique and present."""
    if frame.empty:
        raise SourceFormatError(source, f"{item}: no NAV rows")
    if frame["obs_date"].duplicated().any():
        raise SourceFormatError(source, f"{item}: a NAV date appears twice")
    return frame.sort_values("obs_date").reset_index(drop=True)


def header_lines(rows: Sequence[Sequence[str | None]]) -> tuple[list[str], int | None]:
    """The text cells without a digit of each row above the first data row, and that row.

    A cell is its text, or None when it holds a number or a date. The first data row is the
    first row holding such a cell, or holding only cells with a digit; its position is None
    when no such row is found in the first `INSPECT_ROWS` rows. Cells with a digit are never
    shown, so no value and no date is printed.
    """
    lines: list[str] = []
    for position, row in enumerate(rows[:INSPECT_ROWS]):
        filled = [cell for cell in row if cell is None or cell.strip()]
        if not filled:
            continue
        if any(cell is None for cell in filled):
            return lines, position
        shown = [cell.strip() for cell in filled if cell is not None and not _DIGIT.search(cell)]
        if not shown:
            return lines, position
        lines.append(f"row {position}: " + " | ".join(shown))
    return lines, None
