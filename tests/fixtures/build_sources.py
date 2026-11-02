"""Write the binary fixtures of the source adapters in `sources/`.

Run from the repository root with `uv run python tests/fixtures/build_sources.py`. Every value
is synthetic and every date is before 2023. The text fixtures (FRED and ECB JSON, the Kenneth
French factor CSVs, the Shiller page) are written by hand; this script zips the CSVs as the
library does, writes the portfolio CSVs (49 industries and size deciles, monthly and daily,
with the library's title and column wording), writes `ie_data.xls` in the layout of
Shiller's file, and writes the SPDR `navhist.xlsx` in a synthetic layout. The iShares
SpreadsheetML files and product pages are written by hand. The helpers are also used by
the tests to build variants with a renamed column or a missing section.
"""

import io
import zipfile
from collections.abc import Mapping, Sequence
from pathlib import Path
from xml.sax.saxutils import escape

import xlwt  # type: ignore[import-untyped]

from tiferet.data.source_config import load_sources

SOURCES = Path(__file__).resolve().parent / "sources"
FRENCH, SHILLER, SPDR = SOURCES / "french", SOURCES / "shiller", SOURCES / "spdr"
ZIP_TIME = (2022, 12, 1, 0, 0, 0)
ZIP_CREATE_SYSTEM = 3

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


def deterministic_zip(members: Mapping[str, bytes]) -> bytes:
    """A zip of `members`, identical on every operating system and Python build.

    Members are written sorted by name with a fixed timestamp. `ZipInfo.create_system` is
    fixed to Unix (3): by default `zipfile` records the creating system, which is 0 on
    Windows and 3 elsewhere, so the same members would give different bytes.
    """
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name in sorted(members):
            info = zipfile.ZipInfo(name, ZIP_TIME)
            info.create_system = ZIP_CREATE_SYSTEM
            archive.writestr(info, members[name])
    return buffer.getvalue()


def zip_csv(text: str, member: str) -> bytes:
    """A deterministic zip holding `text` as its one member."""
    return deterministic_zip({member: text.encode("latin-1")})


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


XlsxCell = str | float | None

SPDR_HEADER = ("Date", "NAV", "Shares Outstanding", "Total Net Assets")
SPDR_ROWS: tuple[tuple[XlsxCell, ...], ...] = (
    ("30-Dec-2022", 61.84, 1200000.0, 74208000.0),
    ("29-Dec-2022", 61.27, 1200000.0, 73524000.0),
    ("23-Dec-2022", "60.95", 1180000.0, 71921000.0),
    ("14-Apr-2022", 58.02, 1100000.0, 63822000.0),
)


def _column(index: int) -> str:
    name = ""
    index += 1
    while index:
        index, rest = divmod(index - 1, 26)
        name = chr(65 + rest) + name
    return name


def _sheet_xml(rows: Sequence[Sequence[XlsxCell]]) -> str:
    body = []
    for r, row in enumerate(rows, start=1):
        cells = []
        for c, value in enumerate(row):
            ref = f"{_column(c)}{r}"
            if isinstance(value, str):
                text = escape(value)
                cells.append(f'<c r="{ref}" t="inlineStr"><is><t>{text}</t></is></c>')
            elif value is not None:
                cells.append(f'<c r="{ref}"><v>{value!r}</v></c>')
        body.append(f'<row r="{r}">{"".join(cells)}</row>')
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f"<sheetData>{''.join(body)}</sheetData></worksheet>"
    )


def xlsx(sheets: dict[str, Sequence[Sequence[XlsxCell]]]) -> bytes:
    """A minimal deterministic xlsx workbook with inline strings and numbers."""
    main_ns = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    rel_ns = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
    pkg_ns = "http://schemas.openxmlformats.org/package/2006/relationships"
    sheet_type = f"{rel_ns}/worksheet"
    names = list(sheets)
    parts = {
        "[Content_Types].xml": (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" '
            'ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/xl/workbook.xml" ContentType="application/'
            'vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
            + "".join(
                f'<Override PartName="/xl/worksheets/sheet{i}.xml" ContentType="application/'
                'vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
                for i in range(1, len(names) + 1)
            )
            + "</Types>"
        ),
        "_rels/.rels": (
            f'<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="{pkg_ns}">'
            f'<Relationship Id="rId1" Type="{rel_ns}/officeDocument" Target="xl/workbook.xml"/>'
            "</Relationships>"
        ),
        "xl/workbook.xml": (
            f'<?xml version="1.0" encoding="UTF-8"?><workbook xmlns="{main_ns}" '
            f'xmlns:r="{rel_ns}"><sheets>'
            + "".join(
                f'<sheet name="{escape(name)}" sheetId="{i}" r:id="rId{i}"/>'
                for i, name in enumerate(names, start=1)
            )
            + "</sheets></workbook>"
        ),
        "xl/_rels/workbook.xml.rels": (
            f'<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="{pkg_ns}">'
            + "".join(
                f'<Relationship Id="rId{i}" Type="{sheet_type}" Target="worksheets/sheet{i}.xml"/>'
                for i in range(1, len(names) + 1)
            )
            + "</Relationships>"
        ),
    }
    for i, name in enumerate(names, start=1):
        parts[f"xl/worksheets/sheet{i}.xml"] = _sheet_xml(sheets[name])
    return deterministic_zip({path: text.encode("utf-8") for path, text in parts.items()})


def spdr_xlsx(
    header: Sequence[str] = SPDR_HEADER, rows: Sequence[Sequence[XlsxCell]] = SPDR_ROWS
) -> bytes:
    """A NAV history workbook in a synthetic layout: title rows, a header, daily rows newest
    first, and a closing note."""
    sheet: list[Sequence[XlsxCell]] = [
        ("SPDR synthetic fixture fund",),
        (),
        ("Fund Name:", "Synthetic Energy Fixture"),
        (),
        tuple(header),
        *rows,
        (),
        ("Past performance is not a reliable indicator of future performance.",),
    ]
    return xlsx({"Notes": [("Synthetic fixture",)], "NAV History": sheet})


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
    SPDR.mkdir(exist_ok=True)
    (SPDR / "navhist.xlsx").write_bytes(spdr_xlsx())


if __name__ == "__main__":
    main()
