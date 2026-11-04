# 0014. Research-history sources and their licences

- Status: Accepted
- Date: 2026-10-27

## Context

Plan §5.1 named Stooq for daily equity and ETF prices and listed the other free sources. The plan publishes only derived results, never raw data. Each source's terms were checked before its adapter was built.

## Decision

**Sources used now.**

| Source | Used for | Terms |
| --- | --- | --- |
| FRED and ALFRED (Federal Reserve Bank of St. Louis) | Macro and rate series with vintages: CPI, unemployment, Treasury yields, federal funds, Nasdaq indices, WTI, Baa spread, Cboe volatility indices (VIX, 3-month VIX, oil, gold, emerging markets) | API key required. Products must state: "This product uses the FRED® API but is not endorsed or certified by the Federal Reserve Bank of St. Louis." Series that FRED marks as copyrighted by a third party need that owner's permission beyond personal use; this applies to NASDAQCOM and NASDAQ100 (Nasdaq), BAA10Y (Moody's) and VIXCLS, VXVCLS, OVXCLS, GVZCLS and VXEEMCLS (Cboe). Their raw values are never republished, and published results derived from them name the owner. |
| ECB Data Portal | Euro area yield curves, €STR, euro reference rates | Free reuse, commercial use included, if the source is cited ("Source: ECB statistics") and the statistics are not modified. Results built on them cite the ECB and state the calculations derived from them. |
| Kenneth French Data Library | US, regional and industry factor and portfolio returns since 1926 | Freely downloadable. The library recomputes its full history every month, so it is a history source, not a live one. Results cite the library. |
| Robert Shiller, shillerdata.com | S&P composite price, dividends, earnings, CPI and long rate since 1871 | Published without a licence and with a disclaimer that the user assumes all risk. The file is revised in place. Results cite the source; raw values are not republished. |
| iShares fund data files | Daily NAV history of the seven iShares UCITS holdings and of US-listed iShares signal ETFs (IWM first), with distributions for the US funds | Personal and internal use only: the German terms say "nur für Ihren persönlichen und internen Gebrauch bestimmt" and forbid publishing "Sekundärwerke"; the US file permits use "solely for your personal, non-commercial use". Used under decision 0016: raw values are never published; results derived from them name iShares. The download link is found on each product page at run time. |
| SPDR NAV history file | Daily NAV of the SPDR S&P US Energy Select Sector UCITS ETF | Website terms not yet read; treated as restricted under decision 0016 until they are. |
| Coin Metrics Community data | Daily price and market capitalization of BTC, ETH and the coins that were among the largest since 2013 | Keyless community API: "API key is not required when accessing community endpoints"; rate limit "10 requests per 6 seconds sliding window for an IP address"; licence CC BY-NC 4.0 (https://gitbook-docs.coinmetrics.io/packages/coin-metrics-community-data.md). The page names `https://api.coinmetrics.io/v4` as the community root, while the API reference and its OpenAPI specification name `https://community-api.coinmetrics.io/v4`; the latter is configured and the owner's `--inspect` run settles it. Which metrics are free per asset is not documented; the catalog's `community` flag is read by `--inspect`. Results credit "Coin Metrics Community Data, CC BY-NC 4.0". |
| DefiLlama stablecoins API | Daily circulating USDT and USDC | Free API at https://api.llama.fi, "no authentication required" (https://api-docs.defillama.com/llms-free.txt). The terms (https://defillama.com/terms) grant a personal, non-commercial licence, forbid republishing "the data in any form without permission", and allow programmatic access only through the "official public API", which is the one used. Used under decision 0016: raw values are never published; results credit DefiLlama. |
| Open Crypto Pricing (Stoeckl and Pukrop) | Survivorship-free crypto factor returns (market, size, momentum), risk-free rate and coin counts, daily, weekly and monthly, from 30 December 2013 | "our factor series, multiverse, evaluation and crosswalk are released under CC BY 4.0"; "The underlying market data are from CoinGecko and CoinMarketCap and subject to their terms" (https://opencryptopricing.com/download.html). Delisted and inactive coins are retained. Updated weekly on Mondays; the full history may be recomputed. The CoinGecko-based investable files are used. Results credit "Open Crypto Asset Pricing, Stoeckl and Pukrop, CC BY 4.0, opencryptopricing.com". |

**Request identity.** Every request carries the honest project User-Agent, `tiferet/<version> (+https://github.com/giacomobandinelli/tiferet)`, and `Accept: */*`, because urllib sends no Accept header and some CDNs refuse requests without one. The first smoke run failed to download Shiller's `ie_data.xls` from its CDN (img1.wsimg.com) with a connection error whose reason was not reported. It is not yet established whether the CDN rejects the project User-Agent. Errors now report the reason, the redirect chain and the final status. If the CDN is shown to reject the User-Agent, whether to change it is the owner's decision; until then it stays.

**Sources dropped or not used.**

- **Stooq** is dropped. Its downloads require an API key since March 2026, and its terms forbid redistribution without consent.
- **Yahoo Finance** is not used. Its terms allow personal use only.

**Crypto: no free source of total market capitalization.** No free, licensed history of total crypto market capitalization exists (checked by the owner on 2026-11-04). CoinGecko's global market cap chart needs the Analyst plan ($129 a month), and CoinMarketCap's global metrics from 2013 need the Startup plan ($79 a month). CoinMarketCap's website terms forbid scraping, so its historical snapshot pages are not used. BTC dominance is therefore replaced by the BTC-to-stablecoin ratio (decision 0017). CoinGecko is deferred to a later nightly cross-check.

**Crypto survivorship.** Coin Metrics tracks the assets it covers today, so coins that were large and later died may be missing from its data, and candidate pools built from it lean towards survivors. Open Crypto Pricing retains delisted and inactive coins. Its market factor is the reference for the crypto market return, and the size of the gap is measured where the two overlap.

**Sources planned next.** World Bank Pink Sheet (monthly commodities). Its terms are checked when its adapter is built. The Cboe volatility indices are taken from FRED, which serves them with vintages through the adapter that already exists. FRED marks them as copyrighted by Cboe, whose terms forbid storing its content "in an electronic retrieval system" without consent, so they are restricted from either source and used under decision 0016.

**Holdings without an adapter.**

- **Xtrackers II EUR Overnight Rate Swap UCITS ETF 1C.** The fund tracks the Solactive €STR +8.5 Daily Total Return Index, and its NAV download requires accepting a gate. Its daily return is modelled as €STR (ECB adapter) plus 8.5 basis points a year, minus the TER from `config/costs_taxes.yaml`.
- **Vanguard EUR Eurozone Government Bond UCITS ETF.** Vanguard publishes no downloadable NAV file: its price export is built in the browser from an internal interface. This is an open gap.

**Open gaps.** No free source has been chosen yet for:

- daily gold, silver and copper prices;
- daily emerging-market equity prices;
- the daily BTP-Bund spread;
- the NAV history of the Vanguard EUR Eurozone Government Bond holding.

## Consequences

- Equity and ETF price histories come from issuer NAV files and index-level proxies rather than one bulk price source, so each fund's adapter carries its own provenance.
- The website and reports carry the FRED attribution sentence and the ECB citation wherever those series feed a published number.
- Results that use the copyrighted FRED series publish only derived figures. Publishing their raw values would need the owners' permission first.
