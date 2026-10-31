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
- ALFRED's first vintage of a series often carries decades of earlier observations. For a row of that first vintage, `known_at` is the end of the observation period plus the series' publication lag, at 23:59:59 New York time, when that is earlier than the first vintage. Such rows are `approximated`; all others are `actual`. This applies only when the first vintage was taken by the development end (2022-12-31). A later first vintage carries post-2022 knowledge, so its rows keep their own date and fall in the holdout.
- An approximated row carries the value of the first vintage, which may include revisions made before it. This is the remaining look-ahead risk of the rule, limited to the years before ALFRED tracked the series.
- Some series cannot be requested with their vintages. FRED answers 400 when a series has no ALFRED history, or when it has more vintage dates than one request allows. The case is recognised only from FRED's own error text, which must contain a phrase from `fred.no_vintage_errors`; any other error stays an error. The series is then built from real-time snapshots, each requested with `realtime_start` and `realtime_end` set to one date:
  - The development-end snapshot (2022-12-31) stamps every row at the end of its period plus the publication lag.
  - At ingestion, a snapshot taken today adds only the values that are new or changed since the development-end snapshot. Each is known no earlier than 23:59:59 New York time on the day of that snapshot.
  - When FRED refuses the development-end snapshot too, today's snapshot is the only source, also in a smoke run, whose observations still stop at the development end. Its rows are then all known from today.

  Every snapshot row is `approximated`. A value of the development-end snapshot carries revisions made before 2023, taken as known from the lag. No value seen only after 2022 is dated before the day it was seen, so post-2022 revisions never reach the development partition. A full refresh replaces today's snapshot each run, so the dates of values first seen on earlier days are kept only once the nightly pipeline merges downloads incrementally.
- For series over the vintage-date limit, splitting the request into real-time windows under the limit would keep their actual vintages. That is left as a later improvement to the snapshot rule.
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
- Tables are found by what the real files contain, as the owner's `--inspect` run showed (2026-10-27):
  - A portfolio table is selected by its title and date width, and exactly one must match. Titles are compared ignoring case, repeated spaces, "Weight" against "Weighted" and a leading "Average": the library writes "Average Value Weight Returns -- Monthly" for size portfolios and "Average Value Weighted Returns -- Monthly" or "-- Daily" for industries and the daily size file.
  - A 3-factor table has no title; a note stands above it. It is the first table with the expected date width whose header holds Mkt-RF, SMB, HML and RF. Annual tables are skipped by their 4-digit dates.
  - A header line may be padded with whitespace and may lack its leading comma, in which case its fields must be exactly the row's value columns. A table with no recognisable header is never guessed at: it fails with the number of such tables, and `--inspect` prints the non-numeric lines directly above it, never a data row, so the layout can be reported.
  - Columns are matched by a series word, not by their spelling: "2-Dec" in the monthly size file and "Dec 2" in the daily one are both `dec2` (`kf_size_dec2_m`, `kf_size_dec2_d`), "Lo 10" is `lo10`, "<= 0" is `le0`. A header with characters outside letters, digits, spaces and hyphens, or two headers with one word, is refused, as is a configured column the table lacks. The other size columns ("Lo 30", "Qnt 2" and so on) are known but not ingested.
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

**TLS.**

- Every HTTPS download verifies the server's certificate and host name against the operating system's certificate store, through the pinned `truststore` package, instead of the bundled list of the Python installation.
- Reason: on the owner's Windows PC, the Shiller download failed with `CERTIFICATE_VERIFY_FAILED: self-signed certificate in certificate chain`, most likely because antivirus software inspects HTTPS downloads and re-signs them with its own root. That root is trusted by the operating system and not by the bundled list. FRED and ECB passed because their connections were not re-signed.
- Verification is never disabled, for one source or for all: no `CERT_NONE`, unverified context, `check_hostname = False` or `verify=False` appears in the package, and a test searches for them. If the failure persists with the system store, the fix is to install the intercepting root in the system store, not to relax the check.

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
- `--inspect` downloads each configured item and prints its structure only, never a value or an observation date:
  - French: each table's title line, column header line, row count and date widths; for a table without a header line, the non-numeric lines above its first row;
  - Shiller: the data link, the sheet names and the text cells above the first data row;
  - ECB: the SDMX dimension ids and series codes;
  - FRED: whether a vintage request up to the development end, and one over the full real-time range, succeed, with FRED's error text if not; observations stop at the development end in both.

  It exits with status 1 when an item could not be inspected. Configuration such as French section titles is corrected from its output.
- A failed download's error gives the server's error text (FRED's `error_message`, truncated to 300 characters), the reason of a connection failure, the redirect chain and the final status, all with keys redacted.
- Ingestion (`--ingest`) runs in production mode, by the owner only.

## Consequences

- Every silver row's `known_at` can be traced to a dated rule or to the source's own vintage record, and the data-auditor can check each lag against its source.
- French and Shiller histories are the latest recomputation projected back with a publication lag, not the values known at the time. Research results that depend on them inherit this, and the referee should test their sensitivity to it.
- Revisions published after an ingestion are not stamped with their own `known_at` until the nightly pipeline compares each download with the stored versions. That comparison is left to the pipeline task.
- A source that shortens its history or changes its layout stops ingestion with a named error instead of overwriting stored data.
- Approximated FRED rows can be tightened by extending each series' lag source, without changing the rule.
