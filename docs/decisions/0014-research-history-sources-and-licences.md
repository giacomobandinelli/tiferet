# 0014. Research-history sources and their licences

- Status: Accepted
- Date: 2026-10-27

## Context

Plan §5.1 named Stooq for daily equity and ETF prices and listed the other free sources. The plan publishes only derived results, never raw data. Each source's terms were checked before its adapter was built.

## Decision

**Sources used now.**

| Source | Used for | Terms |
| --- | --- | --- |
| FRED and ALFRED (Federal Reserve Bank of St. Louis) | Macro and rate series with vintages: CPI, unemployment, Treasury yields, federal funds, Nasdaq indices, WTI, Baa spread | API key required. Products must state: "This product uses the FRED® API but is not endorsed or certified by the Federal Reserve Bank of St. Louis." Series that FRED marks as copyrighted by a third party need that owner's permission beyond personal use; this applies to NASDAQCOM and NASDAQ100 (Nasdaq) and BAA10Y (Moody's). Their raw values are never republished, and published results derived from them name the owner. |
| ECB Data Portal | Euro area yield curves, €STR, euro reference rates | Free reuse, commercial use included, if the source is cited ("Source: ECB statistics") and the statistics are not modified. Results built on them cite the ECB and state the calculations derived from them. |
| Kenneth French Data Library | US, regional and industry factor and portfolio returns since 1926 | Freely downloadable. The library recomputes its full history every month, so it is a history source, not a live one. Results cite the library. |
| Robert Shiller, shillerdata.com | S&P composite price, dividends, earnings, CPI and long rate since 1871 | Published without a licence and with a disclaimer that the user assumes all risk. The file is revised in place. Results cite the source; raw values are not republished. |

**Sources dropped or not used.**

- **Stooq** is dropped. Its downloads require an API key since March 2026, and its terms forbid redistribution without consent.
- **Yahoo Finance** is not used. Its terms allow personal use only.

**Sources planned next.** World Bank Pink Sheet (monthly commodities), Cboe (VIX history), issuer NAV files for UCITS and US-listed ETFs, and Coin Metrics and CoinGecko community data. Each one's terms are checked when its adapter is built.

**Open gaps.** No free source has been chosen yet for:

- daily gold, silver and copper prices;
- daily emerging-market equity prices;
- the daily BTP-Bund spread.

## Consequences

- Equity and ETF price histories come from issuer NAV files and index-level proxies rather than one bulk price source, so each fund's adapter carries its own provenance.
- The website and reports carry the FRED attribution sentence and the ECB citation wherever those series feed a published number.
- Results that use the copyrighted FRED series publish only derived figures. Publishing their raw values would need the owners' permission first.
