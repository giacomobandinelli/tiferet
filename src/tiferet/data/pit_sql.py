"""Point-in-time rule for SQL in DuckDB (plan §8.1; decisions 0003, 0010).

`relation` is the SQL counterpart of `pit.get`: a relation of one series from a silver table
holding the latest version of each observation with `known_at <= as_of`, under the same mode
checks. A DuckDB silver table has columns `series_id VARCHAR, obs_date DATE,
known_at TIMESTAMPTZ, value DOUBLE`. It has no holdout partition of its own, so the
research-mode refusal of a late `as_of` is what keeps holdout rows out.

Values are written into the query as literals rather than bound parameters, because DuckDB
refuses to combine relations that carry parameters of the same name. Every literal is
checked first: names against an identifier pattern, `as_of` and `start` rendered from typed
values.
"""

import re
from collections.abc import Mapping
from datetime import date, datetime

import duckdb
import pandas as pd

from tiferet.data.pit import Mode, check_as_of
from tiferet.data.store import check_series_id

IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)?$")

_LATEST_KNOWN = """
SELECT series_id, obs_date, known_at, value
FROM {table}
WHERE series_id = '{series}'
  AND known_at <= TIMESTAMPTZ '{as_of}'
  AND obs_date >= DATE '{start}'
QUALIFY row_number() OVER (PARTITION BY obs_date ORDER BY known_at DESC) = 1
"""


def check_table(table: str) -> str:
    """Refuse a table name that is not a plain, optionally schema-qualified identifier."""
    if not IDENTIFIER.fullmatch(table):
        raise ValueError(f"invalid table name: {table!r}")
    return table


def relation(
    con: duckdb.DuckDBPyConnection,
    table: str,
    series: str,
    as_of: datetime,
    start: date | None = None,
    *,
    mode: Mode,
    env: Mapping[str, str] | None = None,
) -> duckdb.DuckDBPyRelation:
    """Return `series` from `table` as it was known at `as_of`, one row per `obs_date`."""
    stamp, _ = check_as_of(as_of, mode, env)
    query = _LATEST_KNOWN.format(
        table=check_table(table),
        series=check_series_id(series),
        as_of=stamp.isoformat(),
        start=pd.Timestamp(start).date().isoformat() if start is not None else date.min,
    )
    return con.sql(query).order("obs_date")


def inputs(
    con: duckdb.DuckDBPyConnection,
    table: str,
    roles: Mapping[str, str],
    as_of: datetime,
    start: date | None = None,
    *,
    mode: Mode,
    env: Mapping[str, str] | None = None,
) -> duckdb.DuckDBPyRelation:
    """Return the inputs of a SQL feature: columns `input, obs_date, known_at, value, as_of`.

    `roles` maps each input role of the feature to the series that fills it; each series is
    read through `relation`, so every input obeys the same point-in-time rule. `as_of` is
    repeated on every row so the feature can place its last decision date.
    """
    if not roles:
        raise ValueError("a feature needs at least one input")
    stamp, _ = check_as_of(as_of, mode, env)
    columns = f"obs_date, known_at, value, TIMESTAMPTZ '{stamp.isoformat()}' AS as_of"
    parts = []
    for role, series in roles.items():
        check_series_id(role)
        part = relation(con, table, series, as_of, start, mode=mode, env=env)
        parts.append(part.select(f"'{role}' AS input, {columns}"))
    combined = parts[0]
    for part in parts[1:]:
        combined = combined.union(part)
    return combined
