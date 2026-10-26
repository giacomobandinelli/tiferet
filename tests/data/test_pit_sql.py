"""Tests for the SQL point-in-time helper (decisions 0003, 0010)."""

from collections.abc import Iterator
from datetime import date

import duckdb
import pandas as pd
import pytest

from tiferet.data import pit_sql
from tiferet.data.pit import (
    DEVELOPMENT_END,
    UNSEAL_FLAG,
    HoldoutSealedError,
    Mode,
    NaiveTimestampError,
)

ROWS = """
INSERT INTO silver VALUES
    ('cpi', '2022-10-01', '2022-11-15 07:00+00', 100.0),
    ('cpi', '2022-11-01', '2022-12-15 07:00+00', 101.0),
    ('cpi', '2022-10-01', '2022-12-15 07:00+00', 100.5),
    ('cpi', '2022-12-01', '2023-01-16 07:00+00', 102.0),
    ('gdp', '2022-07-01', '2022-10-28 08:00+00', 5.0)
"""


@pytest.fixture
def con() -> Iterator[duckdb.DuckDBPyConnection]:
    connection = duckdb.connect()
    connection.execute(
        "CREATE TABLE silver (series_id VARCHAR, obs_date DATE, known_at TIMESTAMPTZ, value DOUBLE)"
    )
    connection.execute(ROWS)
    yield connection
    connection.close()


def at(text: str) -> pd.Timestamp:
    return pd.Timestamp(text, tz="UTC")


def values(relation: duckdb.DuckDBPyRelation) -> list[float]:
    return [float(v) for v in relation.df()["value"]]


def test_latest_version_known_at_as_of(con: duckdb.DuckDBPyConnection) -> None:
    before = pit_sql.relation(con, "silver", "cpi", at("2022-12-15 06:59"), mode=Mode.RESEARCH)
    after = pit_sql.relation(con, "silver", "cpi", DEVELOPMENT_END, mode=Mode.RESEARCH, env={})
    assert values(before) == [100.0]
    assert values(after) == [100.5, 101.0]


def test_start_filters_observations(con: duckdb.DuckDBPyConnection) -> None:
    relation = pit_sql.relation(
        con, "silver", "cpi", DEVELOPMENT_END, date(2022, 11, 1), mode=Mode.RESEARCH, env={}
    )
    assert values(relation) == [101.0]


def test_research_refuses_a_moment_after_development_end(con: duckdb.DuckDBPyConnection) -> None:
    with pytest.raises(HoldoutSealedError):
        pit_sql.relation(con, "silver", "cpi", at("2023-01-20"), mode=Mode.RESEARCH, env={})


def test_unsealed_and_production_read_current_data(con: duckdb.DuckDBPyConnection) -> None:
    unsealed = {UNSEAL_FLAG: "1"}
    research = pit_sql.relation(
        con, "silver", "cpi", at("2023-02-01"), mode=Mode.RESEARCH, env=unsealed
    )
    production = pit_sql.relation(con, "silver", "cpi", at("2023-02-01"), mode=Mode.PRODUCTION)
    assert values(research) == values(production) == [100.5, 101.0, 102.0]


def test_naive_as_of_is_refused(con: duckdb.DuckDBPyConnection) -> None:
    with pytest.raises(NaiveTimestampError):
        pit_sql.relation(con, "silver", "cpi", pd.Timestamp("2022-06-01"), mode=Mode.PRODUCTION)


@pytest.mark.parametrize("table", ["silver; DROP TABLE silver", "a.b.c", "1silver", "'x'"])
def test_table_that_is_not_an_identifier_is_refused(
    con: duckdb.DuckDBPyConnection, table: str
) -> None:
    with pytest.raises(ValueError, match="invalid table name"):
        pit_sql.relation(con, table, "cpi", DEVELOPMENT_END, mode=Mode.RESEARCH, env={})


def test_series_that_is_not_an_identifier_is_refused(con: duckdb.DuckDBPyConnection) -> None:
    with pytest.raises(ValueError, match="invalid series id"):
        pit_sql.relation(con, "silver", "cpi' OR '1'='1", DEVELOPMENT_END, mode=Mode.RESEARCH)


def test_inputs_label_each_role(con: duckdb.DuckDBPyConnection) -> None:
    relation = pit_sql.inputs(
        con,
        "silver",
        {"price": "cpi", "growth": "gdp"},
        DEVELOPMENT_END,
        mode=Mode.RESEARCH,
        env={},
    )
    frame = relation.df()
    assert sorted(frame["input"].unique()) == ["growth", "price"]
    assert len(frame) == 3
    assert (frame["as_of"] == DEVELOPMENT_END).all()


def test_inputs_need_a_role(con: duckdb.DuckDBPyConnection) -> None:
    with pytest.raises(ValueError, match="at least one input"):
        pit_sql.inputs(con, "silver", {}, DEVELOPMENT_END, mode=Mode.RESEARCH, env={})


def test_nothing_known_yet_gives_no_rows(con: duckdb.DuckDBPyConnection) -> None:
    relation = pit_sql.relation(con, "silver", "cpi", at("2022-01-01"), mode=Mode.RESEARCH)
    assert relation.df().empty
