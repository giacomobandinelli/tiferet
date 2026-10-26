"""Reference trend features (plan §3.3), SQL versions for DuckDB."""

import duckdb

from tiferet.features.registry import register_sql
from tiferet.features.sql_view import INPUTS, aligned_query, input_rows
from tiferet.features.trend import PRICE, SMA_WINDOW

_SMA = f"""
SELECT obs_date, available_at,
       CASE WHEN count(*) OVER w = {SMA_WINDOW} THEN avg(value) OVER w END AS value
FROM ({input_rows(PRICE.role)}) AS price
WINDOW w AS (ORDER BY obs_date ROWS BETWEEN {SMA_WINDOW - 1} PRECEDING AND CURRENT ROW)
"""


@register_sql("sql_sma_200", PRICE)
def sql_sma_200(inputs: duckdb.DuckDBPyRelation) -> duckdb.DuckDBPyRelation:
    """Trailing simple moving average of the price over 200 observations."""
    result = inputs.query(INPUTS, aligned_query(_SMA))
    if result is None:
        raise RuntimeError("the sql_sma_200 query returned no relation")
    return result
