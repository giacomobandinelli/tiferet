"""Write the binary fixtures of the source adapters in `sources/`.

Run from the repository root with `uv run python tests/fixtures/build_sources.py`. Every value
is synthetic and every date is before 2023. The text fixtures (FRED and ECB JSON, the Kenneth
French factor CSVs, the Shiller page) are written by hand; this script zips the CSVs as the
library does, writes the portfolio CSVs (49 industries and size deciles, monthly and daily,
with the library's title and column wording), and writes `ie_data.xls` in the layout of
Shiller's file. The helpers are also used by the tests to build variants with a renamed
column or a missing section.
"""

import io
import zipfile
from collections.abc import Sequence
from pathlib import Path

import xlwt  # type: ignore[import-untyped]

from tiferet.data.source_config import load_sources

SOURCES = Path(__file__).resolve().parent / "sources"
FRENCH, SHILLER = SOURCES / "french", SOURCES / "shiller"
ZIP_TIME = (2022, 12, 1, 0, 0, 0)

FRENCH_ZIPS = {
    "F-F_Research_Data_Factors_CSV.zip": ("factors_monthly.csv", "F-F_Research_Data_Factors.CSV"),
    "F-F_Research_Data_Factors_daily_CSV.zip": (
        "factors_daily.csv",
        "F-F_Research_Data_Factors_daily.CSV",
    ),
    "49_Industry_Portfolios_CSV.zip": ("ind49_monthly.csv", "49_Industry_Portfolios.CSV"),
    "49_Industry_Portfolios_daily_CSV.zip": (
        "ind49_daily.csv",
        "49_Industry_Portfolios_daily.CSV",
    ),
    "Portfolios_Formed_on_ME_CSV.zip": ("size_monthly.csv", "Portfolios_Formed_on_ME.CSV"),
    "Portfolios_Formed_on_ME_Daily_CSV.zip": (
        "size_daily.csv",
        "Portfolios_Formed_on_ME_Daily.CSV",
    ),
    "Asia_Pacific_ex_Japan_3_Factors_Daily_CSV.zip": (
        "asia_pacific_daily.csv",
        "Asia_Pacific_ex_Japan_3_Factors_Daily.csv",
    ),
}

MONTHS = ["202206", "202207", "202208", "202209"]
DAYS = ["20220929", "20220930", "20221003", "20221031"]
SIZE_MONTHLY_COLUMNS = (
    ["<= 0", "Lo 30", "Med 40", "Hi 30", "Lo 20", "Qnt 2", "Qnt 3", "Qnt 4", "Hi 20", "Lo 10"]
    + [f"{n}-Dec" for n in range(2, 10)]
    + ["Hi 10"]
)
SIZE_DAILY_COLUMNS = (
    ["<= 0", "Lo 30", "Med 40", "Hi 30", "Lo 20", "Qnt 2", "Qnt 3", "Qnt 4", "Hi 20", "Lo 10"]
    + [f"Dec {n}" for n in range(2, 10)]
    + ["Hi 10"]
)

SHILLER_HEADER = (
    "Date",
    "P",
    "D",
    "E",
    "CPI",
    "Fraction",
    "Rate GS10",
    "Price",
    "Dividend",
    "Price",
    "Earnings",
    "Earnings",
    "CAPE",
)
ShillerRow = tuple[float, float, float | None, float | None, float, float, float]
SHILLER_ROWS: tuple[ShillerRow, ...] = (
    (2021.10, 4566.5, 59.0, 190.0, 276.6, 2021.79, 1.58),
    (2022.04, 4391.3, 61.0, 196.0, 289.1, 2022.29, 2.75),
    (2022.07, 3911.7, 64.0, 194.0, 296.3, 2022.54, 2.90),
    (2022.10, 3726.1, 65.0, None, 298.0, 2022.79, 3.98),
    (2022.11, 3917.5, 65.5, None, 297.7, 2022.88, 3.89),
    (2022.12, 3912.4, None, None, 296.8, 2022.96, 3.62),
)


def zip_csv(text: str, member: str) -> bytes:
    """A deterministic zip holding `text` as its one member."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(zipfile.ZipInfo(member, ZIP_TIME), text.encode("latin-1"))
    return buffer.getvalue()


def _section(title: str, columns: Sequence[str], dates: Sequence[str], seed: int) -> list[str]:
    lines = [f"  {title}", "," + ",".join(f"{c:<5}" for c in columns)]
    for row, date in enumerate(dates):
        values = [((seed + 7 * row + 3 * col) % 23 - 11) / 4 for col in range(len(columns))]
        if seed == 1 and row == 1:
            values[2] = -999.0
        lines.append(date + "," + ",".join(f"{v:7.2f}" for v in values))
    return [*lines, ""]


def portfolios_csv(
    preamble: Sequence[str], columns: Sequence[str], sections: Sequence[tuple[str, Sequence[str]]]
) -> str:
    """A portfolio file: free-text notes, then one titled table per section."""
    lines = [*preamble, ""]
    for seed, (title, dates) in enumerate(sections, start=1):
        lines += _section(title, columns, dates, seed)
    return "\n".join([*lines, "Copyright 2022 Synthetic Fixture"]) + "\n"


def ind49_csv(columns: Sequence[str], *, daily: bool = False) -> str:
    """A 49-industry file with value- and equal-weighted returns and a firm-count section."""
    if daily:
        sections = [
            ("Average Value Weighted Returns -- Daily", DAYS),
            ("Average Equal Weighted Returns -- Daily", DAYS),
            ("Number of Firms in Portfolios", DAYS),
        ]
    else:
        sections = [
            ("Average Value Weighted Returns -- Monthly", MONTHS),
            ("Average Equal Weighted Returns -- Monthly", MONTHS),
            ("Average Value Weighted Returns -- Annual", ["2020", "2021"]),
            ("Number of Firms in Portfolios", MONTHS),
        ]
    preamble = [
        "This file was created using a synthetic test database.",
        "It contains value- and equal-weighted returns for 49 industry portfolios.",
        "Missing data are indicated by -99.99 or -999.",
    ]
    return portfolios_csv(preamble, columns, sections)


def size_csv(*, daily: bool = False) -> str:
    """A size-decile file. The library words the titles and the deciles differently in the
    monthly file ("Weight", "2-Dec") and the daily file ("Weighted", "Dec 2")."""
    if daily:
        columns = SIZE_DAILY_COLUMNS
        sections = [
            ("Average Value Weighted Returns -- Daily", DAYS),
            ("Average Equal Weighted Returns -- Daily", DAYS),
        ]
    else:
        columns = SIZE_MONTHLY_COLUMNS
        sections = [
            ("Average Value Weight Returns -- Monthly", MONTHS),
            ("Average Equal Weight Returns -- Monthly", MONTHS),
            ("Average Value Weight Returns -- Annual", ["2020", "2021"]),
            ("Number of Firms in Portfolios", MONTHS),
        ]
    preamble = [
        "This file was created using a synthetic test database.",
        "It contains returns for portfolios formed on size.",
        "Missing data are indicated by -99.99 or -999.",
    ]
    return portfolios_csv(preamble, columns, sections)


def shiller_xls(
    header: Sequence[str] = SHILLER_HEADER, rows: Sequence[ShillerRow] = SHILLER_ROWS
) -> bytes:
    """A workbook laid out like `ie_data.xls`: notes, a header row, monthly rows, a note."""
    book = xlwt.Workbook()
    book.add_sheet("Notes").write(0, 0, "Synthetic fixture")
    sheet = book.add_sheet("Data")
    sheet.write(0, 0, "Stock Market Data Used in Synthetic Fixture")
    sheet.write(5, 1, "S&P")
    sheet.write(5, 6, "Long Interest")
    for column, label in enumerate(header):
        sheet.write(7, column, label)
    for offset, row in enumerate(rows):
        for column, value in enumerate(row):
            if value is not None:
                sheet.write(8 + offset, column, value)
    sheet.write(8 + len(rows), 0, "Notes: values are synthetic.")
    buffer = io.BytesIO()
    book.save(buffer)
    return buffer.getvalue()


def main() -> None:
    ind49 = next(f for f in load_sources().french.files if f.dataset == "ind49")
    (FRENCH / "ind49_monthly.csv").write_text(ind49_csv(ind49.columns), encoding="latin-1")
    (FRENCH / "ind49_daily.csv").write_text(
        ind49_csv(ind49.columns, daily=True), encoding="latin-1"
    )
    (FRENCH / "size_monthly.csv").write_text(size_csv(), encoding="latin-1")
    (FRENCH / "size_daily.csv").write_text(size_csv(daily=True), encoding="latin-1")
    for archive, (source, member) in FRENCH_ZIPS.items():
        text = (FRENCH / source).read_text(encoding="latin-1")
        (FRENCH / archive).write_bytes(zip_csv(text, member))
    (SHILLER / "ie_data.xls").write_bytes(shiller_xls())


if __name__ == "__main__":
    main()
