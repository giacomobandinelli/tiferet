"""Tests for the SpreadsheetML 2003 reader."""

import pytest

from tiferet.data.errors import SourceFormatError
from tiferet.data.spreadsheetml import BLANK, Cell, escape_bare_ampersands, read_workbook

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
    sheets = read_workbook(book(sheet("One", rows), sheet("Two", "")), "test", "fund").sheets
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
    assert read_workbook(content, "test", "fund").sheets["S"] == [[Cell("String", "xy")]]


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
    with pytest.raises(SourceFormatError, match=f"^test: fund: .*{match}"):
        read_workbook(content, "test", "fund")


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("iShares Core S&P 500 UCITS ETF", "iShares Core S&P 500 UCITS ETF"),
        ("erfolgt durch S&P, Moody's und Fitch", "erfolgt durch S&P, Moody's und Fitch"),
        ("x#!type=ishares&style=All&view=list", "x#!type=ishares&style=All&view=list"),
        ("A &amp; B", "A & B"),
        ("it&#8217;s", "it\u2019s"),
        ("it&#x2019;s", "it\u2019s"),
        ("&lt;&gt;&quot;&apos;", "<>\"'"),
        ("a & b &", "a & b &"),
        ("&#;&#x;&1;", "&#;&#x;&1;"),
    ],
)
def test_bare_ampersands_are_escaped_and_references_kept(text: str, expected: str) -> None:
    content = book(sheet("S", f"<ss:Row>{cell(text)}</ss:Row>"))
    assert read_workbook(content, "test", "fund").sheets["S"] == [[Cell("String", expected)]]


def test_the_escaped_ampersands_are_counted() -> None:
    rows = f"<ss:Row>{cell('S&P &amp; Moody&#8217;s &#x2019;')}{cell('a&b&c')}</ss:Row>"
    assert read_workbook(book(sheet("S", rows)), "test", "fund").escaped_ampersands == 3
    assert read_workbook(book(sheet("S", "")), "test", "fund").escaped_ampersands == 0


def test_an_ampersand_in_an_attribute_is_escaped() -> None:
    content = book(sheet("S&P", f"<ss:Row>{cell('x')}</ss:Row>"))
    assert list(read_workbook(content, "test", "fund").sheets) == ["S&P"]


def test_comments_processing_instructions_and_cdata_are_left_as_they_are() -> None:
    body = b"<?pi a&b?><!-- S&P --><a><![CDATA[S&P &amp;]]>S&P</a>"
    assert escape_bare_ampersands(body) == (
        b"<?pi a&b?><!-- S&P --><a><![CDATA[S&P &amp;]]>S&amp;P</a>",
        1,
    )
    rows = f"<ss:Row>{cell('<![CDATA[S&P &amp;]]>')}</ss:Row>"
    assert read_workbook(book(sheet("S", rows)), "test", "fund").sheets["S"] == [
        [Cell("String", "S&P &amp;")]
    ]


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("&nbsp;", r"undefined entity"),
        ("<b>x</ss:Data>", r"mismatched tag"),
        ("a < b", r"not well-formed \(invalid token\)"),
    ],
)
def test_other_xml_errors_still_raise_with_the_parser_position(text: str, message: str) -> None:
    content = book(sheet("S", f"<ss:Row>{cell(text)}</ss:Row>"))
    pattern = rf"^test: fund: the fund data file is not XML \({message}: line \d+, column \d+\)$"
    with pytest.raises(SourceFormatError, match=pattern):
        read_workbook(content, "test", "fund")
