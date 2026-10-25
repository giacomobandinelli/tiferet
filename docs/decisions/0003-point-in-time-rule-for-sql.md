# 0003. One point-in-time rule for Python and SQL

- Status: Accepted
- Date: 2026-10-25

## Context

The plan reads data only through `pit.get(series, as_of=date)` (§8.1, §10.1). Part of the feature library, however, is written in SQL in DuckDB (§9), and dbt builds the gold tables by reading R2 directly (§6). Adapters must also read raw files to ingest them. Routing every one of these reads through a Python function is impractical, but leaving SQL unconstrained would open a route to look-ahead.

## Decision

- Every silver table carries a `known_at` column: the date on which each value became known (§5.2).
- SQL features and dbt gold models filter on `known_at <= as_of`.
- `pit.get` is the Python entry point to the same rule.
- Raw ingestion by the adapters in `src/tiferet/data/` is exempt, because it runs before values are stamped.
- The locked look-ahead test covers SQL features as well as Python features.

## Consequences

- The point-in-time guarantee is a property of the data model, not of a single function, so it holds in Python, SQL and dbt alike.
- Every silver model must populate `known_at`. A dbt test must check that it is never null and never earlier than the observation date.
- The locked test must be able to run SQL features against its synthetic series.
- Reviewers check SQL for the `known_at` filter just as they check Python for `pit.get`.
