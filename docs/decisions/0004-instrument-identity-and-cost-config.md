# 0004. Instrument identity and cost parameters in separate files

- Status: Accepted
- Date: 2026-10-25

## Context

The plan placed fees in `config/holdings.yaml` (§3.1, §9). It also required every cost number to live in `config/costs_taxes.yaml` (§3.8, §8.1). A fund's TER would therefore have had two possible homes, and the sourcing check could pass on one file while the other drifted.

## Decision

- `config/holdings.yaml` holds instrument identity only: ISIN, tax class, launch date, `exposure_available_from`, and the sources for each.
- All fees, including each fund's TER, and every tax parameter live in `config/costs_taxes.yaml`, each with its source URL and the date it was checked.

## Consequences

- Each cost number has exactly one place, so the sourcing check in CI covers every cost the model uses.
- Code that needs both identity and fees for a holding joins the two files on ISIN, or on the instrument key for holdings without one, such as directly held crypto.
- Re-verifying fees is a change to `costs_taxes.yaml` alone.
