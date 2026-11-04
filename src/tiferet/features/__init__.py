"""Feature library: SQL in DuckDB and Python.

Importing this package registers every feature module, so the registry is complete.
"""

from tiferet.features import crypto, sql_trend, trend

__all__ = ["crypto", "sql_trend", "trend"]
