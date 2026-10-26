# 0010. Point-in-time semantics

- Status: Accepted
- Date: 2026-10-26

## Context

The plan requires that look-ahead be impossible by construction (§10.1). Python reads stamped data only through `pit.get(series, as_of=date)`, SQL filters on `known_at <= as_of` (§8.1, 0003), and research mode refuses dates after 2022-12-31 (§4, 0002, 0008). The plan does not fix several things:

- the storage model;
- what "known at" means for revised values and for publication times within a day;
- how the cutoff handles time zones;
- how features with several inputs of different frequencies line up in time;
- what the locked look-ahead test has to prove.

## Decision

**Storage model.** Every silver series is one long table with these columns:

| Column | Meaning |
| --- | --- |
| `series_id` | The series |
| `obs_date` | The period a value refers to, as a naive date |
| `known_at` | When that version became known, as a UTC timestamp |
| `value` | The value |

A revision is a new row with a later `known_at`; earlier versions are never overwritten. The pandera schema in `src/tiferet/data/schema.py` enforces the following:

- `known_at` is timezone-aware, and a naive `known_at` is refused rather than assumed to be UTC.
- `obs_date` is a naive midnight.
- No column holds nulls.
- `(series_id, obs_date, known_at)` is unique.
- `known_at` is never earlier than `obs_date`.

A point-in-time view holds one version per `obs_date`.

**Accessor.** `pit.get(series, as_of, start=None, *, mode, store, env=None)` returns, for each `obs_date` from `start` on, the latest version with `known_at <= as_of`. It never returns a version known later.

- `as_of` must be timezone-aware; a naive one raises `NaiveTimestampError`.
- `mode` is a required `Mode` (`RESEARCH` or `PRODUCTION`) and has no default.
- Stores implement a small `Store` protocol. The current implementations are in memory and Parquet under `data/silver/`. DuckDB and R2 stores come later.

**Holdout boundary.** The holdout partition holds every version with `known_at` at or after 2023-01-01 00:00 Europe/Berlin. The split is on when a value became known, not on the period it refers to, so a December 2022 value published in January 2023 is holdout.

In research mode, an `as_of` later than 2022-12-31 23:59:59 Europe/Berlin raises `HoldoutSealedError`, and the store is asked not to open the holdout partition at all, unless `holdout_unsealed(env)` is true. That function takes a mapping, which defaults to the process environment, and is true only when `TIFERET_UNSEAL_HOLDOUT` equals `"1"` (0008). Tests pass plain dictionaries and never set the flag in the process environment. Production mode reads both partitions.

**SQL.** `pit_sql.relation` returns a DuckDB relation of one series with the same rule and the same mode checks. `pit_sql.inputs` combines several series into a feature's input relation with columns `input, obs_date, known_at, value, as_of`. A DuckDB table has no holdout partition, so in SQL the research-mode refusal of a late `as_of` is the guard. The table name, series and role names are checked against identifier patterns before they enter a query.

**Timing convention.**

- Adapters set `known_at` to the source's actual publication time for releases, or its close time for prices, in UTC.
- Decision date `d` stands for the moment 23:59:59 Europe/Berlin on `d`. Decision dates are business days. A calendar-day decision calendar for crypto is left to a later decision.
- A decision made at `as_of = t` trades no earlier than the next session of the traded venue. `run_backtest()` enforces this when it is built.

**Features.**

- A feature declares its inputs as a tuple of `InputSpec(role, frequency)`, where frequency is daily, weekly, monthly or quarterly.
- A Python feature takes a mapping from role to `PitSeries` and returns a float series on decision dates. A `PitSeries` holds the values and the `known_at` of one input, indexed by `obs_date`, together with the `as_of` of the view.
- A SQL feature takes the relation from `pit_sql.inputs` and returns a relation with columns `date, value`.
- Features compute per observation. They then place the result on decision dates with `align` (Python) or `aligned_query` (SQL). A result becomes available at the running maximum of `known_at` up to its observation, because a trailing computation depends on every earlier observation. Each value is forward-filled from that moment, so a monthly series enters a daily feature only once it is published.
- The last decision date of a view is the business day of its `as_of`. So `evaluate(feature, views)`, the value at the last decision date, is the value known at `as_of`.
- Features live in a registry in `src/tiferet/features/registry.py`. Importing `tiferet.features` registers them all.

**Locked test.** `tests/locked/test_no_look_ahead.py` iterates the registry. It generates synthetic inputs for each declared spec, with publication lags that cross at least one day boundary, and runs every check once per input:

- A spike in that input changes nothing before the spike's `known_at`, on the point-in-time path and on a single full-history computation.
- Hypothesis checks that the full-history value at random cut dates equals the value computed from history truncated by `pit.get` at the cut.
- A revised value is invisible before its revision `known_at` and visible from then on.
- Two leaky features defined inside the test must be caught: a centred moving average, and a mixed-frequency feature that aligns its monthly input by `obs_date`. This proves the test can fail.

The test covers features only. Label leakage and purged walk-forward splits are tested separately when the cycle-phase model is built (§3.4).

**Type stubs.** `pandas-stubs` 3.0.5.260914 is pinned as a development dependency. No release yet targets pandas 3.1, the pinned runtime version. An API that exists only in 3.1 and is untyped gets a narrow `type: ignore` with its reason.

## Consequences

- Look-ahead through the data layer requires bypassing `pit.get`, `pit_sql` or `align`, which reviewers check for (REVIEW.md §1).
- Adapters must record real publication times. A wrong `known_at` passes every check here, so the data-auditor checks publication dates against their sources.
- Features added to the registry are covered by the locked test with no change to it.
- The truncation property holds for inputs whose `known_at` increases with `obs_date`. Late-published or revised observations are covered by the point-in-time checks instead, because a single full-history computation cannot reproduce earlier vintages.
- The holdout boundary is defined in Berlin time, so values published in the last hour of 2022 UTC fall in the holdout.
