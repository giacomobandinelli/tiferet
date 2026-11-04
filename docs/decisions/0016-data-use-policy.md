# 0016. Data-use policy

- Status: Accepted
- Date: 2026-11-02

## Context

Most free market data is licensed for personal or internal use only:

- **Cboe.** The terms of use (https://www.cboe.com/terms) grant use of the site's content for "personal non-commercial use" and forbid storing it "in an electronic retrieval system" or creating "a derivative work" without Cboe's consent. FRED carries the Cboe volatility indices (VIXCLS, VXVCLS, OVXCLS, GVZCLS, VXEEMCLS) as series copyrighted by Cboe.
- **iShares.** The German terms of use (https://www.ishares.com/de/privatanleger/de/nutzungsbedingungen) state that the site is "nur für Ihren persönlichen und internen Gebrauch bestimmt", not for "kommerziellen Zwecken (mit oder ohne Gewinnerzielungsabsicht)", and that users may not "Sekundärwerke … publizieren". The disclaimer of the iShares US fund data file permits use "solely for your personal, non-commercial use".
- **Other issuers.** WisdomTree limits its content to "personal, non-commercial use" (decision 0015). SPDR's terms were not read for this record; its data is treated as restricted until they are.
- **DefiLlama.** The terms (https://defillama.com/terms) grant a personal, non-commercial licence and forbid republishing "the data in any form without permission" (added 2026-11-04).
- **Open Crypto Pricing.** The factor series are CC BY 4.0, but "the underlying market data are from CoinGecko and CoinMarketCap and subject to their terms" (added 2026-11-04).
- **Copyrighted FRED series.** FRED marks NASDAQCOM and NASDAQ100 (Nasdaq), BAA10Y (Moody's) and the Cboe indices as owned by a third party, whose permission is needed beyond personal use (decision 0014).

Tiferet is public: its repository, website, Excel workbook and PowerPoint report can be read by anyone. Plan §5.1 already said that raw data is never republished; this record states the rule for every output and the reasoning behind it.

## Decision

- **Raw third-party data is never published, committed, exported or offered for download.** This covers the repository, the website (the SQL console included), the Excel workbook, the PowerPoint report and any data download. Test fixtures are synthetic. Raw data is kept only in private storage: the silver and gold tables, and the bronze copies of data that cannot be downloaded again (plan §5.2), are never made public.
- **Published outputs are aggregate results computed from the data**: portfolio returns, risk metrics, weights and signals. Each carries its source attribution: the FRED sentence, the ECB citation ("Source: ECB statistics" with the calculations derived), and the names of the issuers and index owners whose data feeds it (for example "NAV data: iShares, SPDR; volatility indices: Cboe, via FRED").
- **This is a judgement, not a licence.** The project judges that aggregate results computed from many inputs are not a republication of the data. No source has confirmed this, so a residual licence risk remains, and it is stated openly in the methodology page and the README. If a data owner objects, the affected results are withdrawn or rebuilt from another source.
- **Permissive sources are preferred where they serve equally**: ECB (free reuse with citation), the Kenneth French Data Library, Shiller's data, the World Bank, non-copyrighted FRED series, Coin Metrics Community data under CC BY-NC 4.0 and the Open Crypto Pricing factors under CC BY 4.0. A restricted source is used where no permissive one covers the same need, such as fund NAVs, the Cboe volatility indices or DefiLlama's stablecoin histories.

**Credits** (added 2026-11-04). Every source in `config/sources.yaml` carries an `attribution` field, and a test refuses a source without one. The website, the Excel workbook and the PowerPoint report carry the credit of every source that feeds a result they show:

| Source | Credit |
| --- | --- |
| FRED and ALFRED | This product uses the FRED® API but is not endorsed or certified by the Federal Reserve Bank of St. Louis. |
| ECB Data Portal | Source: ECB statistics |
| Kenneth French Data Library | Kenneth R. French Data Library |
| Shiller | Robert J. Shiller, shillerdata.com |
| iShares fund data files | NAV data: iShares |
| SPDR NAV history file | NAV data: SPDR |
| Coin Metrics Community | Coin Metrics Community Data, CC BY-NC 4.0 |
| DefiLlama | DefiLlama |
| Open Crypto Pricing | Open Crypto Asset Pricing, Stoeckl and Pukrop, CC BY 4.0, opencryptopricing.com |

The ECB credit also states the calculations derived from its statistics. Index owners named by a FRED series (Nasdaq, Moody's, Cboe) are named beside the FRED credit wherever their series feed a result. A new source is not complete without its credit.

## Consequences

- The website's Downloads page and SQL console offer derived results only, never a table of source values (plan §7).
- Attribution is part of every output's definition; a published number whose inputs cannot be attributed is a defect.
- The Cboe indices are restricted whether they are taken from Cboe or from FRED, which carries them as Cboe-copyrighted series; FRED is used because it serves them with vintages through one API, not because its copy is licensed more freely.
- Each new source's terms are checked and recorded in decision 0014 before its adapter is built; restricted terms do not block an adapter, but they decide what may be published from it.
- The residual risk is concentrated in the issuer NAV histories and the Cboe indices. Replacing them with permissive sources, where one appears, removes it.
