# 0006. Current German tax law applied to the full history

- Status: Accepted
- Date: 2026-10-25

## Context

Backtests reach back several decades (§3.5), but German tax rules changed often over that period. The flat tax on capital income dates from 2009. The partial exemption and the Vorabpauschale date from the 2018 fund-tax reform. The saver's allowance and the §23 EStG exemption threshold changed in 2023 and 2024. The tax engine (§3.9) could model the rules in force each year, or apply one rule set throughout. It also needs a personal income-tax rate for taxable short-term sales, and the plan did not give one.

## Decision

- The tax engine applies current German tax law to the whole backtest history. This is stated as an explicit assumption wherever after-tax results are shown.
- The personal income-tax rate for taxable short-term private sales (§23 EStG: crypto and Xetra-Gold held 12 months or less) is a parameter in `config/costs_taxes.yaml`. Its default is 42% without solidarity surcharge, sourced to the §32a EStG tariff for 2026.

## Consequences

- After-tax results answer one question: how today's investor would have fared under today's rules. They do not reconstruct the tax actually due in past years.
- The tax engine needs only one rule set, which keeps it smaller and easier to audit against worked examples.
- The portfolio and the benchmark are taxed under the same rules, so the comparison stays fair.
- The personal rate can be changed in configuration without touching code, and every change is sourced and dated like any other parameter.
