"""Reading xls and xlsx workbooks for the adapters, through the calamine engine."""

import io

import pandas as pd

from tiferet.data.errors import SourceFormatError


def read_sheets(
    content: bytes, source: str, what: str, *, keep_default_na: bool = True
) -> dict[str, pd.DataFrame]:
    """Every sheet of the workbook, by name, without a header row.

    With `keep_default_na` false, text such as "n/a" or "NA" stays text instead of becoming a
    missing value, so a strict parser can refuse it.
    """
    try:
        sheets = pd.read_excel(
            io.BytesIO(content),
            sheet_name=None,
            header=None,
            engine="calamine",
            keep_default_na=keep_default_na,
        )
    except Exception:
        raise SourceFormatError(source, f"the {what} is not a readable spreadsheet") from None
    return {str(name): sheet for name, sheet in sheets.items()}
