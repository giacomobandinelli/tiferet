"""Open Crypto Pricing adapter (plan §5.1; decisions 0013, 0014, 0017).

Survivorship-free crypto factor returns by Stoeckl and Pukrop, built from CoinGecko data:
the investable-universe files at daily, weekly and monthly frequency, with the market
(CMKT), size (CSIZE) and momentum (CMOM) factors, the risk-free rate (RF) and the number of
coins (n_coins, daily and weekly files). Each file is a CSV whose header must equal its
configured date columns and value columns exactly, in order; a differing header is named in
the error. Dates are `YYYY-MM-DD`; the weekly file's `week_end` must be six days after its
`week_start`, and the monthly file's `month_start` and `month_end` the first and last day of
one month (its `month` column is a label and is not read). An empty field is a missing
value. Returns are stored as decimal fractions, divided by 100 where a file's `return_units`
is percent; counts are stored as they are.

The files are updated weekly on Mondays and the full history may be recomputed, so this is
a history source like the Kenneth French library: a value is known `publication_lag_days`
after the end of its period, at 23:59:59 UTC, and only the latest download is kept.

Smoke: `uv run python -m tiferet.data.ocp --smoke`.
"""

import csv
import io
import math
import re

import pandas as pd

from tiferet.data.adapter import Batch, combine, stamped
from tiferet.data.cli import Inspection
from tiferet.data.cli import main as run_cli
from tiferet.data.errors import SourceFormatError
from tiferet.data.fetch import Download, Fetcher, FetchError
from tiferet.data.schema import APPROXIMATED
from tiferet.data.source_config import OcpFile, OcpFrequency, ReturnUnits, Sources
from tiferet.data.timing import UTC, end_of_day, plus_days

SOURCE = "ocp"
DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")
WEEK = pd.Timedelta(days=6)


def rows(content: bytes, item: str) -> tuple[list[str], list[list[str]]]:
    """The header and data rows of a CSV file. Blank lines are skipped; a line of commas is
    a row, so a row whose fields are all empty fails its date check."""
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise SourceFormatError(SOURCE, f"{item}: not UTF-8 text") from None
    lines = [r for r in csv.reader(io.StringIO(text)) if len(r) > 1 or any(f.strip() for f in r)]
    if not lines:
        raise SourceFormatError(SOURCE, f"{item}: the file is empty")
    header = [f.strip() for f in lines[0]]
    return header, [[f.strip() for f in r] for r in lines[1:]]


def _dates(values: list[str], file: OcpFile, column: str) -> pd.Series:
    if not all(DAY.fullmatch(v) for v in values):
        raise SourceFormatError(SOURCE, f"{file.path}: {column} is not a YYYY-MM-DD date")
    try:
        dates = pd.to_datetime(pd.Series(values, dtype=str), format="%Y-%m-%d")
    except ValueError:
        raise SourceFormatError(SOURCE, f"{file.path}: {column} is not a valid date") from None
    return dates.astype("datetime64[us]")


def _number(text: str, file: OcpFile, column: str) -> float:
    if text == "":
        return math.nan
    try:
        value = float(text)
    except ValueError:
        raise SourceFormatError(SOURCE, f"{file.path}: a {column} value is not a number") from None
    if not math.isfinite(value):
        raise SourceFormatError(SOURCE, f"{file.path}: a {column} value is not finite")
    if column in file.count_columns and (value < 0 or not value.is_integer()):
        raise SourceFormatError(SOURCE, f"{file.path}: a {column} value is not a count")
    return value


def parse(content: bytes, file: OcpFile) -> pd.DataFrame:
    """Columns `obs_date`, `period_end` and one per configured value column."""
    header, data = rows(content, file.path)
    expected = [*file.date_columns, *file.columns]
    if header != expected:
        raise SourceFormatError(
            SOURCE, f"{file.path}: header is {', '.join(header)}; expected {', '.join(expected)}"
        )
    if any(len(r) != len(expected) for r in data):
        raise SourceFormatError(SOURCE, f"{file.path}: a row's width differs from its header")
    columns = {name: [r[i] for r in data] for i, name in enumerate(expected)}
    start = _dates(columns[file.start_column], file, file.start_column)
    if file.end_column is None:
        end = start
    else:
        end = _dates(columns[file.end_column], file, file.end_column)
        match file.frequency:
            case OcpFrequency.WEEKLY:
                if not ((end - start) == WEEK).all():
                    raise SourceFormatError(SOURCE, f"{file.path}: a week does not span seven days")
            case OcpFrequency.MONTHLY:
                if (
                    not (start.dt.day == 1).all()
                    or not (end == (start + pd.offsets.MonthEnd(0)).astype("datetime64[us]")).all()
                ):
                    raise SourceFormatError(
                        SOURCE,
                        f"{file.path}: a month does not run from its first to its last day",
                    )
            case OcpFrequency.DAILY:
                pass
    if not start.is_monotonic_increasing or start.duplicated().any():
        raise SourceFormatError(SOURCE, f"{file.path}: dates are not unique and increasing")
    wide = pd.DataFrame({"obs_date": start, "period_end": end})
    scale = 100.0 if file.return_units is ReturnUnits.PERCENT else 1.0
    for column in file.columns:
        values = pd.Series([_number(v, file, column) for v in columns[column]], dtype=float)
        wide[column] = values if column in file.count_columns else values / scale
    return wide


def all_missing(wide: pd.DataFrame, file: OcpFile) -> list[str]:
    """Series ids of the file's columns in which every value is missing."""
    return [file.series_id(c) for c in file.columns if wide[c].isna().all()]


def stamp(wide: pd.DataFrame, file: OcpFile, lag: int) -> list[pd.DataFrame]:
    """Each value is known `lag` days after the end of its period, at 23:59:59 UTC."""
    known_at = end_of_day(plus_days(wide["period_end"], lag), UTC)
    frames = []
    for column in file.columns:
        present = wide[column].notna()
        frames.append(
            stamped(
                file.series_id(column),
                wide.loc[present, "obs_date"],
                known_at[present],
                wide.loc[present, column],
                APPROXIMATED,
            )
        )
    return frames


def collect(fetcher: Fetcher, sources: Sources, *, smoke: bool) -> Batch:
    config = sources.ocp
    downloads: list[Download] = []
    frames: list[pd.DataFrame] = []
    missing: list[str] = []
    for file in config.files.value:
        result = fetcher.get(config.base_url + file.path)
        wide = parse(result.content, file)
        frames += stamp(wide, file, config.publication_lag_days.value)
        missing += all_missing(wide, file)
        downloads.append(result)
    expected = tuple(i for f in config.files.value for i in f.series_ids())
    return Batch(SOURCE, tuple(downloads), combine(frames), expected, frozenset(missing))


def inspect(fetcher: Fetcher, sources: Sources) -> Inspection:
    """Each file's header and row count; never a date or a value."""
    lines: list[str] = []
    failures = 0
    for file in sources.ocp.files.value:
        try:
            header, data = rows(fetcher.get(sources.ocp.base_url + file.path).content, file.path)
        except (FetchError, SourceFormatError) as error:
            lines.append(f"{file.path}: {error}")
            failures += 1
            continue
        expected = [*file.date_columns, *file.columns]
        verdict = "as configured" if header == expected else "differs from the configuration"
        lines += [f"{file.path}: {len(data)} rows", f"  header ({verdict}): {', '.join(header)}"]
        failures += header != expected
    return Inspection(lines, failures)


if __name__ == "__main__":
    raise SystemExit(run_cli(SOURCE, collect, inspect=inspect))
