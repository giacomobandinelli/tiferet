# 0005. Locations of the point-in-time accessor and the backtest runner

- Status: Accepted
- Date: 2026-10-25

## Context

The plan names `pit.get` and `run_backtest()` (§8.1) but does not place them in the repository structure (§9). `run_backtest()` writes to the trial ledger, which is I/O, while the code standards keep I/O to adapters and the pipeline (§10.2).

## Decision

- The point-in-time accessor lives in `src/tiferet/data/pit.py`.
- `run_backtest()` and the ledger writer live in a new module, `src/tiferet/research/`, where I/O is allowed.

## Consequences

- The accessor sits beside the adapters that fill the data it serves.
- The pure core (features, weights, costs, tax) stays free of I/O. `src/tiferet/research/` joins adapters and the pipeline as a place where I/O is allowed.
- `src/tiferet/research/` (code) and the top-level `research/` folder (pre-registration, ledger, referee reports) share a name. The first holds the runner; the second holds its records.
