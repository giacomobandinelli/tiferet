"""Reader for SpreadsheetML 2003 workbooks, the XML text format of the iShares fund data files.

A workbook is `<Workbook>` with one `<Worksheet ss:Name="...">` per sheet, each holding a
`<Table>` of `<Row>`s of `<Cell>`s; a cell's value is in `<Data ss:Type="...">`. A row or cell
may skip positions with `ss:Index` (1-based), and a cell may span columns with
`ss:MergeAcross`. The file is read with the standard library XML parser; a document type
declaration is refused, so no entity is ever expanded.
"""

import xml.etree.ElementTree as ElementTree
from dataclasses import dataclass

from tiferet.data.errors import SourceFormatError

NS = "urn:schemas-microsoft-com:office:spreadsheet"
_SS = f"{{{NS}}}"
BOM = b"\xef\xbb\xbf"
EMPTY = ""
"""The type of a cell without data."""


@dataclass(frozen=True)
class Cell:
    """A cell's `ss:Type` (String, Number, DateTime, Boolean or Error; empty without data)
    and its text."""

    kind: str
    text: str


BLANK = Cell(EMPTY, "")

Sheet = list[list[Cell]]


def _position(element: ElementTree.Element, current: int, source: str, what: str) -> int:
    """The 0-based position of a row or cell: its `ss:Index`, else the next one."""
    index = element.get(f"{_SS}Index")
    if index is None:
        return current
    if not index.isdigit() or int(index) - 1 < current:
        raise SourceFormatError(source, f"a {what} has an invalid ss:Index")
    return int(index) - 1


def _cell(element: ElementTree.Element) -> Cell:
    data = element.find(f"{_SS}Data")
    if data is None:
        return BLANK
    return Cell(data.get(f"{_SS}Type", EMPTY), "".join(data.itertext()).strip())


def _row(element: ElementTree.Element, source: str) -> list[Cell]:
    cells: list[Cell] = []
    for item in element.findall(f"{_SS}Cell"):
        position = _position(item, len(cells), source, "cell")
        cells.extend([BLANK] * (position - len(cells)))
        cells.append(_cell(item))
        merge = item.get(f"{_SS}MergeAcross", "0")
        if not merge.isdigit():
            raise SourceFormatError(source, "a cell has an invalid ss:MergeAcross")
        cells.extend([BLANK] * int(merge))
    while cells and cells[-1] == BLANK:
        cells.pop()
    return cells


def _sheet(element: ElementTree.Element, source: str) -> Sheet:
    rows: Sheet = []
    for table in element.findall(f"{_SS}Table"):
        for item in table.findall(f"{_SS}Row"):
            position = _position(item, len(rows), source, "row")
            rows.extend([] for _ in range(position - len(rows)))
            rows.append(_row(item, source))
    return rows


def read_workbook(content: bytes, source: str) -> dict[str, Sheet]:
    """Every sheet of the workbook, by name, as rows of cells."""
    body = content.removeprefix(BOM).lstrip()
    if b"<!DOCTYPE" in body.upper() or b"<!ENTITY" in body.upper():
        raise SourceFormatError(source, "the fund data file declares a document type")
    try:
        root = ElementTree.fromstring(body)
    except ElementTree.ParseError:
        raise SourceFormatError(source, "the fund data file is not XML") from None
    if root.tag != f"{_SS}Workbook":
        raise SourceFormatError(source, "the fund data file is not a SpreadsheetML workbook")
    sheets: dict[str, Sheet] = {}
    for element in root.findall(f"{_SS}Worksheet"):
        name = element.get(f"{_SS}Name")
        if not name:
            raise SourceFormatError(source, "a worksheet has no name")
        if name in sheets:
            raise SourceFormatError(source, f"two worksheets are named {name!r}")
        sheets[name] = _sheet(element, source)
    if not sheets:
        raise SourceFormatError(source, "the fund data file has no worksheet")
    return sheets
