# 0016. Data-use policy

- Status: Accepted
- Date: 2026-11-02

## Context

Most free market data is licensed for personal or internal use only:

- **Cboe.** The terms of use (https://www.cboe.com/terms) grant use of the site's content for "personal non-commercial use" and forbid storing it "in an electronic retrieval system" or creating "a derivative work" without Cboe's consent. FRED carries the Cboe volatility indices (VIXCLS, VXVCLS, OVXCLS, GVZCLS, VXEEMCLS) as series copyrighted by Cboe.
- **iShares.** The German terms of use (https://www.ishares.com/de/privatanleger/de/nutzungsbedingungen) state that the site is "nur für Ihren persönlichen und internen Gebrauch bestimmt", not for "kommerziellen Zwecken (mit oder ohne Gewinnerzielungsabsicht)", and that users may not "Sekundärwerke … publizieren". The disclaimer of the iShares US fund data file permits use "solely for your personal, non-commercial use".
- **Other issuers.** WisdomTree limits its content to "personal, non-commercial use" (decision 0015). SPDR's terms were not read for this record; its data is treated as restricted until they are.
- **Copyrighted FRED series.** FRED marks NASDAQCOM and NASDAQ100 (Nasdaq), BAA10Y (Moody's) and the Cboe indices as owned by a third party, whose permission is needed beyond personal use (decision 0014).

Tiferet is public: its repository, website, Excel workbook and PowerPoint report can be read by anyone. Plan §5.1 already said that raw data is never republished; this record states the rule for every output and the reasoning behind it.

## Decision

- **Raw third-party data is never published, committed, exported or offered for download.** This covers the repository, the website (the SQL console included), the Excel workbook, the PowerPoint report and any data download. Test fixtures are synthetic. Raw data is kept only in private storage: the silver and gold tables, and the bronze copies of data that cannot be downloaded again (plan §5.2), are never made public.
- **Published outputs are aggregate results computed from the data**: portfolio returns, risk metrics, weights and signals. Each carries its source attribution: the FRED sentence, the ECB citation ("Source: ECB statistics" with the calculations derived), and the names of the issuers and index owners whose data feeds it (for example "NAV data: iShares, SPDR; volatility indices: Cboe, via FRED").
- **This is a judgement, not a licence.** The project judges that aggregate results computed from many inputs are not a republication of the data. No source has confirmed this, so a residual licence risk remains, and it is stated openly in the methodology page and the README. If a data owner objects, the affected results are withdrawn or rebuilt from another source.
- **Permissive sources are preferred where they serve equally**: ECB (free reuse with citation), the Kenneth French Data Library, Shiller's data, the World Bank, non-copyrighted FRED series, and Coin Metrics Community data under CC BY-NC 4.0. A restricted source is used where no permissive one covers the same need, such as fund NAVs or the Cboe volatility indices.

## Consequences

- The website's Downloads page and SQL console offer derived results only, never a table of source values (plan §7).
- Attribution is part of every output's definition; a published number whose inputs cannot be attributed is a defect.
- The Cboe indices are restricted whether they are taken from Cboe or from FRED, which carries them as Cboe-copyrighted series; FRED is used because it serves them with vintages through one API, not because its copy is licensed more freely.
- Each new source's terms are checked and recorded in decision 0014 before its adapter is built; restricted terms do not block an adapter, but they decide what may be published from it.
- The residual risk is concentrated in the issuer NAV histories and the Cboe indices. Replacing them with permissive sources, where one appears, removes it.
