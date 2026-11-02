"""The committed binary fixtures are what `tests/fixtures/build_sources.py` writes.

Archives are compared by member names and contents, not by raw bytes, which carry metadata
that depends on the operating system that wrote them. Text fixtures are read with explicit
line endings, so the tests pass under any checkout.
"""

import pandas as pd
import pytest

from data.adapter_support import FIXTURES, assert_same_archive, fixture
from fixtures.build_sources import FRENCH_ZIPS, shiller_xls, zip_csv
from tiferet.data.excel import read_sheets


@pytest.mark.parametrize("archive", sorted(FRENCH_ZIPS))
def test_french_archives_hold_their_csv_fixture(archive: str) -> None:
    source, member = FRENCH_ZIPS[archive]
    text = (FIXTURES / "french" / source).read_bytes().decode("latin-1").replace("\r\n", "\n")
    assert_same_archive(fixture("french", archive), zip_csv(text, member))


def test_the_shiller_workbook_holds_what_the_builder_writes() -> None:
    committed = read_sheets(fixture("shiller", "ie_data.xls"), "test", "workbook")
    rebuilt = read_sheets(shiller_xls(), "test", "workbook")
    assert list(committed) == list(rebuilt)
    for name in committed:
        pd.testing.assert_frame_equal(committed[name], rebuilt[name])
