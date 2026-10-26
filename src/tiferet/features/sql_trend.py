"""Reference trend features (plan §3.3), SQL versions for DuckDB."""

import duckdb

from tiferet.features.registry import register_sql
from tiferet.features.sql_view import INPUTS, aligned_query, input_rows
from tiferet.features.trend import CPI, PRICE, SMA_WINDOW

_SMA = f"""
SELECT obs_date, available_at,
       CASE WHEN count(*) OVER w = {SMA_WINDOW} THEN avg(value) OVER w END AS value
FROM ({input_rows(PRICE.role)}) AS price
WINDOW w AS (ORDER BY obs_date ROWS BETWEEN {SMA_WINDOW - 1} PRECEDING AND CURRENT ROW)
"""

_REAL_PRICE = f"""
SELECT price.date, price.value / cpi.value AS value
FROM ({aligned_query(input_rows(PRICE.role))}) AS price
JOIN ({aligned_query(input_rows(CPI.role))}) AS cpi USING (date)
ORDER BY price.date
"""


def _run(inputs: duckdb.DuckDBPyRelation, query: str, name: str) -> duckdb.DuckDBPyRelation:
    result = inputs.query(INPUTS, query)
    if result is None:
        raise RuntimeError(f"the {name} query returned no relation")
    return result


@register_sql("sql_sma_200", PRICE.with_warmup(SMA_WINDOW))
def sql_sma_200(inputs: duckdb.DuckDBPyRelation) -> duckdb.DuckDBPyRelation:
    """Trailing simple moving average of the price over 200 observations."""
    return _run(inputs, aligned_query(_SMA), "sql_sma_200")


@register_sql("sql_real_price", PRICE.with_warmup(1), CPI.with_warmup(1))
def sql_real_price(inputs: duckdb.DuckDBPyRelation) -> duckdb.DuckDBPyRelation:
    """Daily price divided by the monthly CPI, each CPI value used only once it was known."""
    return _run(inputs, _REAL_PRICE, "sql_real_price")
