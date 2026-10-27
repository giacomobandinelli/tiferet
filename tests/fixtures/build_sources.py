"""Write the binary fixtures of the source adapters in `sources/`.

Run from the repository root with `uv run python tests/fixtures/build_sources.py`. Every value
is synthetic and every date is before 2023. The text fixtures (FRED and ECB JSON, the Kenneth
French CSVs, the Shiller page) are written by hand; this script zips the CSVs as the library
does, writes the 49-industry CSV, and writes `ie_data.xls` in the layout of Shiller's file.
The helpers are also used by the tests to build variants with a renamed column or a missing
section.
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
}

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


def ind49_csv(columns: Sequence[str]) -> str:
    """A 49-industry file with monthly, annual and firm-count sections."""
    months = ["202206", "202207", "202208", "202209"]
    lines = [
        "This file was created using a synthetic test database.",
        "It contains value- and equal-weighted returns for 49 industry portfolios.",
        "Missing data are indicated by -99.99 or -999.",
        "",
        *_section("Average Value Weighted Returns -- Monthly", columns, months, 1),
        *_section("Average Equal Weighted Returns -- Monthly", columns, months, 2),
        *_section("Average Value Weighted Returns -- Annual", columns, ["2020", "2021"], 3),
        *_section("Number of Firms in Portfolios", columns, months, 4),
        "Copyright 2022 Synthetic Fixture",
    ]
    return "\n".join(lines) + "\n"


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
    for archive, (source, member) in FRENCH_ZIPS.items():
        text = (FRENCH / source).read_text(encoding="latin-1")
        (FRENCH / archive).write_bytes(zip_csv(text, member))
    (SHILLER / "ie_data.xls").write_bytes(shiller_xls())


if __name__ == "__main__":
    main()
