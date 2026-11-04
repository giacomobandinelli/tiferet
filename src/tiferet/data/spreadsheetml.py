"""Reader for SpreadsheetML 2003 workbooks, the XML text format of the iShares fund data files.

A workbook is `<Workbook>` with one `<Worksheet ss:Name="...">` per sheet, each holding a
`<Table>` of `<Row>`s of `<Cell>`s; a cell's value is in `<Data ss:Type="...">`. A row or cell
may skip positions with `ss:Index` (1-based), and a cell may span columns with
`ss:MergeAcross`. The file is read with the standard library XML parser; a document type
declaration is refused, so no entity is ever expanded.

**Bare ampersands.** The iShares files are not well-formed XML: text holds a bare "&", as in a
fund name "iShares Core S&P 500 UCITS ETF", a German note "... erfolgt durch S&P, Moody's und
Fitch ..." and a URL query "...#!type=ishares&style=All&view=..." (observed by the owner on
2026-11-04 in five of the eight configured files). Before parsing, every "&" that does not
start a reference (`&name;`, `&#digits;` or `&#xhex;`) is replaced with "&amp;", so the parser
reads it as the character the issuer wrote. Comments, processing instructions and CDATA
sections are left as they are, since a "&" in them is already literal. A reference to an
undefined entity is not repaired, and every other XML error still raises a format error with
the parser's message and position (decision 0013).
"""

import re
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


@dataclass(frozen=True)
class Workbook:
    """Every sheet of a workbook, by name, and the number of bare ampersands escaped."""

    sheets: dict[str, Sheet]
    escaped_ampersands: int


_LITERAL = re.compile(rb"(<!--.*?-->|<\?.*?\?>|<!\[CDATA\[.*?\]\]>)", re.DOTALL)
"""Markup in which a "&" is literal: comments, processing instructions, CDATA sections."""
_BARE_AMPERSAND = re.compile(rb"&(?!(?:[A-Za-z_:][A-Za-z0-9_:.\-]*|#[0-9]+|#x[0-9A-Fa-f]+);)")
"""A "&" that does not start an entity or character reference."""


def escape_bare_ampersands(body: bytes) -> tuple[bytes, int]:
    """The document with every bare "&" outside literal markup replaced with "&amp;", and the
    number replaced."""
    parts = _LITERAL.split(body)
    count = 0
    for at in range(0, len(parts), 2):
        parts[at], found = _BARE_AMPERSAND.subn(b"&amp;", parts[at])
        count += found
    return b"".join(parts), count


def _position(element: ElementTree.Element, current: int, source: str, item: str, what: str) -> int:
    """The 0-based position of a row or cell: its `ss:Index`, else the next one."""
    index = element.get(f"{_SS}Index")
    if index is None:
        return current
    if not index.isdigit() or int(index) - 1 < current:
        raise SourceFormatError(source, f"{item}: a {what} has an invalid ss:Index")
    return int(index) - 1


def _cell(element: ElementTree.Element) -> Cell:
    data = element.find(f"{_SS}Data")
    if data is None:
        return BLANK
    return Cell(data.get(f"{_SS}Type", EMPTY), "".join(data.itertext()).strip())


def _row(element: ElementTree.Element, source: str, item: str) -> list[Cell]:
    cells: list[Cell] = []
    for child in element.findall(f"{_SS}Cell"):
        position = _position(child, len(cells), source, item, "cell")
        cells.extend([BLANK] * (position - len(cells)))
        cells.append(_cell(child))
        merge = child.get(f"{_SS}MergeAcross", "0")
        if not merge.isdigit():
            raise SourceFormatError(source, f"{item}: a cell has an invalid ss:MergeAcross")
        cells.extend([BLANK] * int(merge))
    while cells and cells[-1] == BLANK:
        cells.pop()
    return cells


def _sheet(element: ElementTree.Element, source: str, item: str) -> Sheet:
    rows: Sheet = []
    for table in element.findall(f"{_SS}Table"):
        for child in table.findall(f"{_SS}Row"):
            position = _position(child, len(rows), source, item, "row")
            rows.extend([] for _ in range(position - len(rows)))
            rows.append(_row(child, source, item))
    return rows


def read_workbook(content: bytes, source: str, item: str) -> Workbook:
    """Every sheet of the workbook, by name, as rows of cells.

    `item` names the file in every format error, such as the series id of a fund.
    """

    def refuse(detail: str) -> SourceFormatError:
        return SourceFormatError(source, f"{item}: {detail}")

    body = content.removeprefix(BOM).lstrip()
    if b"<!DOCTYPE" in body.upper() or b"<!ENTITY" in body.upper():
        raise refuse("the fund data file declares a document type")
    body, escaped = escape_bare_ampersands(body)
    try:
        root = ElementTree.fromstring(body)
    except ElementTree.ParseError as error:
        raise refuse(f"the fund data file is not XML ({error})") from None
    if root.tag != f"{_SS}Workbook":
        raise refuse("the fund data file is not a SpreadsheetML workbook")
    sheets: dict[str, Sheet] = {}
    for element in root.findall(f"{_SS}Worksheet"):
        name = element.get(f"{_SS}Name")
        if not name:
            raise refuse("a worksheet has no name")
        if name in sheets:
            raise refuse(f"two worksheets are named {name!r}")
        sheets[name] = _sheet(element, source, item)
    if not sheets:
        raise refuse("the fund data file has no worksheet")
    return Workbook(sheets, escaped)
