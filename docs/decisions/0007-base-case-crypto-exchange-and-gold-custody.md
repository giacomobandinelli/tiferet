# 0007. Base-case crypto exchange and Xetra-Gold custody charge

- Status: Accepted
- Date: 2026-10-25

## Context

The cost model (§3.8) charges crypto trades at an exchange's published fee schedule and network fees, but the plan does not name an exchange. Xetra-Gold has no management fee, but Clearstream Europe charges a custody fee of currently about 0.3% p.a. (0.025% of the holding per calendar month) plus VAT to the custodian bank. Whether that charge reaches the investor depends on the bank holding the securities account, and the reference broker's treatment is not published.

## Decision

- Kraken (Kraken Pro spot schedule) is the base-case crypto exchange. Its maker-taker fee tiers are set by 30-day spot volume in USD, so the cost model converts EUR trade volume to USD at the trade date's exchange rate to find the tier. Withdrawal fees to cold storage are Kraken's published per-coin fees.
- The full Xetra-Gold custody charge is assumed to be passed on: 0.3% p.a. plus VAT at the standard rate of 19%, an effective 0.357% p.a., deducted from the holding over time.

## Consequences

- Crypto costs follow one published schedule, and each fee is sourced and dated in `config/costs_taxes.yaml` like any other parameter. Using another exchange is a configuration change.
- The gold custody assumption is conservative: a broker that absorbs the charge would make Xetra-Gold cheaper than modelled, never more expensive.
- The custody fee and the VAT rate are separate parameters, so a change in either is re-sourced on its own.
