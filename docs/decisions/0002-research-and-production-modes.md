# 0002. Research and production modes for holdout data

- Status: Accepted
- Date: 2026-10-25

## Context

Development data ends on 31 December 2022, and data from 1 January 2023 onward is the holdout (plan §4). The point-in-time loader refuses any later date unless an unseal flag is set. The nightly pipeline, however, ingests current data every day (§6), the website publishes a nightly data-quality page (§5.3), and the live record publishes monthly target weights from launch (§4). A single blanket refusal would stop the platform from running. A blanket permission would let post-2022 data reach models and backtests before the strategy is frozen.

## Decision

Code runs in one of two modes.

- **Research mode** covers backtests, optimization and model selection. The point-in-time loader refuses any date after 2022-12-31 unless the unseal flag is set.
- **Production mode** covers nightly ingestion, data-quality pages and, after the phase 5 freeze, the live weights. It may read current data. Before the freeze, post-2022 data is only ingested and stored, never used by any model or backtest.

The holdout is stored in R2 under a separate, encrypted prefix. `data/holdout/` is its local equivalent and is gitignored. Agents never read either location.

## Consequences

- Ingestion and data-quality checks cover the full history from day one, so the holdout is complete and tested when it is unsealed.
- Every code path that reads data must declare its mode, and the loader must enforce the research-mode cutoff.
- Before the freeze, no published page may show model output or backtest results computed on post-2022 data.
- The locked test that fails when a development run touches holdout data applies to research mode.
