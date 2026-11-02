"""Tests for the SpreadsheetML 2003 reader."""

import pytest

from tiferet.data.errors import SourceFormatError
from tiferet.data.spreadsheetml import BLANK, Cell, read_workbook

HEAD = '<?xml version="1.0"?><ss:Workbook xmlns:ss="urn:schemas-microsoft-com:office:spreadsheet">'


def book(*sheets: str) -> bytes:
    return (HEAD + "".join(sheets) + "</ss:Workbook>").encode("utf-8")


def sheet(name: str, rows: str) -> str:
    return f'<ss:Worksheet ss:Name="{name}"><ss:Table>{rows}</ss:Table></ss:Worksheet>'


def cell(text: str, kind: str = "String", attrs: str = "") -> str:
    return f'<ss:Cell{attrs}><ss:Data ss:Type="{kind}">{text}</ss:Data></ss:Cell>'


def test_sheets_rows_and_cells() -> None:
    rows = (
        f"<ss:Row>{cell('a')}{cell('1.5', 'Number')}</ss:Row>"
        f'<ss:Row ss:Index="3">{cell("b", attrs=' ss:Index="2"')}</ss:Row>'
        f"<ss:Row>{cell('c', attrs=' ss:MergeAcross="1"')}{cell('d')}</ss:Row>"
    )
    sheets = read_workbook(book(sheet("One", rows), sheet("Two", "")), "test")
    assert list(sheets) == ["One", "Two"]
    assert sheets["One"] == [
        [Cell("String", "a"), Cell("Number", "1.5")],
        [],
        [BLANK, Cell("String", "b")],
        [Cell("String", "c"), BLANK, Cell("String", "d")],
    ]
    assert sheets["Two"] == []


def test_rich_text_and_a_byte_order_mark() -> None:
    rows = '<ss:Row><ss:Cell><ss:Data ss:Type="String"><b>x</b>y</ss:Data></ss:Cell></ss:Row>'
    content = b"\xef\xbb\xbf\n" + book(sheet("S", rows))
    assert read_workbook(content, "test")["S"] == [[Cell("String", "xy")]]


@pytest.mark.parametrize(
    ("content", "match"),
    [
        (b"not xml", "not XML"),
        (b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "b">]><x/>', "declares a document type"),
        (b"<Workbook/>", "not a SpreadsheetML workbook"),
        (book(), "no worksheet"),
        (book(sheet("S", ""), sheet("S", "")), "two worksheets are named 'S'"),
        (book("<ss:Worksheet><ss:Table/></ss:Worksheet>"), "a worksheet has no name"),
        (
            book(sheet("S", f"<ss:Row>{cell('a')}{cell('b', attrs=' ss:Index="1"')}</ss:Row>")),
            "a cell has an invalid ss:Index",
        ),
        (book(sheet("S", '<ss:Row ss:Index="x"/>')), "a row has an invalid ss:Index"),
    ],
)
def test_malformed_workbooks_are_refused(content: bytes, match: str) -> None:
    with pytest.raises(SourceFormatError, match=match):
        read_workbook(content, "test")
