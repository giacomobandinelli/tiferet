# 0013. known_at rules for the research-history sources

- Status: Accepted
- Date: 2026-10-27

## Context

Decision 0010 requires adapters to set `known_at` to the moment a source published each value, and makes look-ahead through the data layer depend on that being right. The first four adapters each need their own rule:

- FRED and ALFRED record release dates for most series, but not release times, and not for the years before a series' first vintage.
- The ECB publishes at fixed times.
- Kenneth French and Robert Shiller publish files whose whole history is recomputed at every update.

Raw files must be ingested without exposing the holdout, and agents must not download live data at all.

## Decision

**Common rules.**

- `known_at` is never earlier than the source's publication. When the exact time is unknown, the rule takes a later moment.
- Every lag and publication time is a sourced value in `config/sources.yaml`, with source URL, check date and verification flag, like the cost and tax parameters (0004). A value that could not be read on its source is marked unverified with the reason.
- Silver tables gain an optional `vintage_kind` column, which extends the storage model of 0010:
  - `actual`: `known_at` is the publication date the source itself records for this version (ALFRED vintages).
  - `approximated`: `known_at` follows a publication rule.
- Moments are computed in the source's time zone and stored in UTC. `HOLDOUT_START` (2023-01-01 00:00 Europe/Berlin) splits the rows between the development and holdout roots.

**FRED and ALFRED.**

- Each series is requested over the full real-time range, so every vintage arrives as one row per value and real-time period.
- A version is known at 23:59:59 America/New_York on its `realtime_start`.
- ALFRED's first vintage of a series often carries decades of earlier observations. For a row of that first vintage, `known_at` is the end of the observation period plus the series' publication lag, at 23:59:59 New York time, when that is earlier than the first vintage. Such rows are `approximated`; all others are `actual`.
- An approximated row carries the value of the first vintage, which may include revisions made before it. This is the remaining look-ahead risk of the rule, limited to the years before ALFRED tracked the series.
- Missing values (`.`) are dropped. A vintage that withdraws a value, marking an observation `.` after a numeric version, cannot be stored, because silver values are never null. The earlier version then stays the latest one in the point-in-time view after the withdrawal. This shows a stale value, never a later one.

**Shiller.**

- The `ie_data.xls` link is looked up on shillerdata.com at each run.
- Prices are monthly averages of daily closes, so a month's price is known at the end of that month (lag 0).
- Dividends and earnings are interpolated from quarterly totals. They are known at the end of the calendar quarter that holds the month, plus 183 days: the longest 10-K deadline of 90 days plus about one quarter for compilation and interpolation.
- CPI and the long rate use the publication lag of their FRED equivalents (CPIAUCSL and DGS10).
- Early years are interpolated from annual figures, so a month can depend on the figure of the year after it. Before each series' `annual_before` year, a month is known at the end of the following calendar year plus the series' lag:

  | Series | `annual_before` | Basis |
  | --- | --- | --- |
  | Dividends and earnings | 1926 | Annual Cowles data before 1926 (stated by Shiller) |
  | CPI | 1913 | Warren and Pearson index before the CPI-U; frequency not stated, treated as annual |
  | Long rate | 1953 | Source not named; the monthly 10-year yield starts in April 1953 |

  Treating source data as annual when it is in fact monthly only delays `known_at`.
- Values before 1938 were compiled decades later (Cowles published in 1938). Their `known_at` dates when the underlying information existed, not when the series did.
- Every Shiller row is `approximated`.

**Kenneth French.**

- A value is known 90 days after the end of the month that holds its date, at 23:59:59 New York time; daily values wait for the end of their month.
- The 90 days come from observed release timing. On 2026-10-27 the data library page showed August 2026 as its latest month, at least 57 days after that month's end. 90 days adds a margin of about one monthly update cycle.
- This rests on one observation of a monthly cadence with irregular timing, so the margin errs late. A later observation of a longer delay raises the value.
- Missing values (-99.99, -999) are dropped. Returns are stored as decimal fractions. The market return of each 3-factor file is derived as Mkt-RF + RF.
- Every row is `approximated`.

**ECB.**

- A value is known a fixed number of TARGET business days after its observation date, at a published local time in Europe/Berlin:

  | Dataset | Release used |
  | --- | --- |
  | Euro area yield curves | Next TARGET day at 12:00. The ECB updates the curves at noon but does not say which market day a release holds. |
  | €STR | Next TARGET day at 09:00. Published at 08:00 and revisable once at 09:00. |
  | Euro reference rates | Same day at 17:00. Published at about 16:00. |

- The TARGET calendar treats New Year, Good Friday, Easter Monday, 1 May, 25 and 26 December, and 31 December in 1999–2001 as closed. A day wrongly treated as closed only delays `known_at`.
- Every ECB row is `approximated`.

**Ingestion.**

- Each run is a full refresh: every download holds the whole history of its series, and the writer replaces both partition files of every series.
- Before any file is written, a data-loss guard compares each series with its stored files in both partitions. It refuses the whole run, keeping every file, when a series:
  - comes back empty;
  - loses any stored observation date;
  - has fewer rows than stored by more than `silver.max_row_loss_fraction` (0.1%).

  Revised values are allowed.
- Parsers check the structure they expect and raise a format error that names the source and the missing element: column names, section titles, the Shiller header cells, the SDMX dimensions, FRED's count and paging. A format error writes nothing.
- The bronze manifest is appended only after the silver files are written. It records the source, the URL with keys redacted, the retrieval time, the SHA-256 and the size of every downloaded file.
- Raw files are not kept: every file here can be downloaded again. The French and Shiller files recompute their history, though, so their past versions are lost. The manifest hashes show when a file changed.

**No live downloads by agents.** Tests run on hand-built fixtures in each source's format, dated before 2023. For the whole session, a guard in `tests/conftest.py` stops the test process from connecting, resolving host names or sending datagrams; subprocesses are not covered. The fetch layer retries transient failures, bodies cut short included, and gives up rather than wait longer than two minutes when a server asks it to. Each adapter has a smoke command for the owner: `uv run python -m tiferet.data.<source> --smoke`.

- It downloads every configured item, parses and stamps it, and prints row counts per partition. It prints no values and writes nothing.
- FRED and ECB smoke requests stop at 2022-12-31.
- Ingestion (`--ingest`) runs in production mode, by the owner only.

## Consequences

- Every silver row's `known_at` can be traced to a dated rule or to the source's own vintage record, and the data-auditor can check each lag against its source.
- French and Shiller histories are the latest recomputation projected back with a publication lag, not the values known at the time. Research results that depend on them inherit this, and the referee should test their sensitivity to it.
- Revisions published after an ingestion are not stamped with their own `known_at` until the nightly pipeline compares each download with the stored versions. That comparison is left to the pipeline task.
- A source that shortens its history or changes its layout stops ingestion with a named error instead of overwriting stored data.
- Approximated FRED rows can be tightened by extending each series' lag source, without changing the rule.
