# 0015. Broad-commodities holding

- Status: Accepted
- Date: 2026-11-02

## Context

Plan §3.1 holds broad commodities in the inflation role. The holding was WisdomTree Broad Commodities UCITS ETF (IE00BKY4W127). Its NAV history is needed for the investable-history comparison of §3.1.1, and the planned issuer-NAV adapter (decision 0014) must be able to read it and publish results derived from it.

## Decision

The holding is iShares Diversified Commodity Swap UCITS ETF (Acc), IE00BDFL4P12. The role (inflation), the tax class (`commodity_fund`) and the currency rule (unhedged) are unchanged. Values were read by the owner on 2026-10-27 from the issuer's fund data file, sheet "Key Facts": benchmark Bloomberg Commodity Index Total Return, TER 0.19%, launch 2017-07-18, accumulating, domiciled in Ireland, USD share class.

Reasons:

- **Same index exposure.** The previous holding tracked the same index, the Bloomberg Commodity Index Total Return. Roles, signals and the pre-launch proxy history are unchanged.
- **Licence.** WisdomTree's terms of use limit site content to "personal, non-commercial use" and forbid using it "for public or commercial purposes, without WisdomTree's written permission" (https://www.wisdomtree.com/corporate/terms). Its NAV history therefore cannot feed the public pipeline.
- **Data source.** iShares publishes a daily NAV history in the same fund data file as the other fund facts. The issuer-NAV adapter planned in 0014 can use it, and it also covers six other holdings.
- **Size and record.** The fund held about USD 2.45 billion of share-class assets on 2026-10-26 and launched in 2017, against 2021 for the previous holding.
- **Structure.** The index return is delivered through total return swaps, with collateral held under UCITS rules. Counterparty risk remains and is noted here; the collateral rules limit it.
- **Cost record.** The previous holding's TER was recorded as 0.19% from the Deutsche Börse listing page, while a third-party profile showed 0.45%. The discrepancy is moot after the switch.

## Consequences

- `config/holdings.yaml` and `config/costs_taxes.yaml` carry the new name, ISIN, launch date and TER, each sourced and verified against the issuer's Key Facts sheet.
- The listed-fund launch in plan §3.1.1 moves from 29 Nov 2021 to 18 Jul 2017, so the proxy period before the launch is shorter.
- `exposure_available_from` (2006-09-22) is kept with its existing source. It records when broad commodity index exposure reached retail investors, which does not depend on the fund chosen.
- Earlier decision records that name the previous holding stay as written.
