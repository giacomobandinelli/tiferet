# Tiferet — Project Plan

Tiferet is a research platform that builds and publishes one portfolio designed to match or beat the S&P 500's return with lower downside variance, after all transaction costs and German taxes, for a euro-based investor. The portfolio only buys, holds, or sells to buy something else; it uses no leverage, no short positions and no derivatives.

This document is the specification the codebase is built from. It defines the objective, the portfolio logic, the research protocol, the data platform, the infrastructure, the outputs and the development workflow.

---

## 1. Objective

**Benchmark.** iShares Core S&P 500 UCITS ETF (Acc), ISIN IE00B5BMR087, bought once and held, taxed under the same German rules as the portfolio.

**Hard requirements for the final portfolio**, measured in EUR after all costs and taxes:

1. Annualized return (CAGR) **equal to or higher** than the benchmark's.
2. Sortino ratio **higher** than the benchmark's, i.e. more return per unit of downside deviation.

Individual signals, sleeves or holdings carry no requirement of their own; only the combined portfolio is judged.

**Reported alongside, not pass/fail:** maximum drawdown, downside deviation, Sharpe ratio, CVaR, results per decade and per crisis, turnover, costs and taxes paid, deflated Sharpe ratio, probability of backtest overfitting, and a 10-month moving-average rule (Faber, 2007) as a simple reference strategy.

**Currency.** Returns are computed in USD where histories are longest, converted to EUR and judged in EUR, the currency in which German tax is assessed. Before 1999, the Deutsche Mark stands in for the euro at the official rate of 1.95583 DEM per EUR. USD results are shown alongside.

**Investor profile.** Private investor, tax resident in Germany. Italy is available as a switch in the tax engine.

---

## 2. Platform components

| Component | Role for the portfolio |
| --- | --- |
| Data warehouse (Cloudflare R2, DuckDB, dbt) | Stores every price, macro series, ETF data point and crypto metric used by signals and backtests |
| Feature library | Turns raw series into standardized features: moving averages, ratios, momentum, volatility, z-scores, events |
| Cycle-phase model | Combines features into calibrated probabilities for each phase of the business cycle |
| Risk ladder and optimizer | Converts phase probabilities into weights, cutting risk step by step from the riskiest assets down |
| Cost model | Charges commissions, spreads and fund fees, with spreads estimated from data |
| German tax engine | Taxes every trade lot by lot |
| Risk and scorecard | Measures the portfolio against the benchmark and reference strategies |
| Research ledger | Records every backtest ever run, for overfitting control |
| AI agents (Claude Code) | Build, test, propose and optimize strategies, and review each other's work |
| Outputs | Website, automated monthly Excel workbook and PowerPoint report, data downloads |

---

## 3. Portfolio design

### 3.1 Holdings

Thirteen holdings, all purchasable by a German retail investor (UCITS ETFs and ETCs with German tax reporting, plus Bitcoin and Ethereum held directly). ISINs and fees were checked in October 2026 and are re-verified before use: instrument identity (ISIN, tax class, launch date, `exposure_available_from`, sources) in `config/holdings.yaml`, and all fees, including TER, in `config/costs_taxes.yaml` (decision 0004).

| Ladder role | Holding | ISIN | TER |
| --- | --- | --- | --- |
| Riskiest | Bitcoin (exchange purchase, cold storage) | – | trading fees only |
| Riskiest | Ethereum (exchange purchase, cold storage) | – | trading fees only |
| High-growth equity | iShares Nasdaq 100 UCITS ETF (Acc) | IE00B53SZB19 | 0.30% |
| Core equity | iShares Core S&P 500 UCITS ETF (Acc) | IE00B5BMR087 | 0.07% |
| Risky equity, other cycles | iShares Core MSCI EM IMI UCITS ETF (Acc) | IE00BKM4GZ66 | 0.18% |
| Late cycle | SPDR S&P US Energy Select Sector UCITS ETF (Acc) | IE00BWBXM492 | 0.15% |
| Inflation | WisdomTree Broad Commodities UCITS ETF | IE00BKY4W127 | 0.19% |
| Inflation, bonds | iShares € Inflation Linked Govt Bond UCITS ETF (Acc) | IE00B0M62X26 | 0.09% |
| Crash and inflation | Xetra-Gold | DE000A0S9GB0 | none |
| Crash, bonds | iShares $ Treasury Bond 7-10yr UCITS ETF EUR Hedged (Acc) | IE000K1VI152 | 0.10% |
| Crash, bonds | Vanguard EUR Eurozone Government Bond UCITS ETF (Acc) | IE00BH04GL39 | 0.07% |
| Cash | Xtrackers II EUR Overnight Rate Swap UCITS ETF 1C | LU0290358497 | 0.10% |
| Cash | iShares USD Treasury Bond 0-1yr UCITS ETF (Acc) | IE00BGSF1X88 | 0.07% |

**Rules**

- Weights between 0% and 100%, summing to 100%; cash never negative.
- Crypto (BTC and ETH combined) capped at 20% of the portfolio, with no minimum allocation. No crypto purchases before 1 January 2017. Each crypto lot is held at least 12 months, so every sale is tax-free under §23 EStG; the model enforces this per lot and reports what the constraint costs.
- US Energy is capped (the index holds about 21 companies).
- Currency is a fixed rule, never traded: equities, commodities, crypto and T-bills unhedged; US Treasuries in the EUR-hedged share class.
- Not modelled: loss of crypto through exchange failure or wallet loss.

### 3.1.1 Investable history

A holding may be held in the backtest only from the date its exposure was available to a German retail investor through some product. Before the listed fund existed, returns come from its underlying index (or the earliest available product tracking it), minus the listed fund's costs; before the exposure was available at all, the holding is excluded and its weight goes to the remaining holdings. Proxy and fund are compared over the years they overlap, and the tracking gap is reported.

| Holding | Listed fund launch | Exposure available to retail investors (approx.) |
| --- | --- | --- |
| Bitcoin | – | 2013; held from 1 January 2017 by rule |
| Ethereum | – | 2015; held from 1 January 2017 by rule |
| Nasdaq 100 | 26 Jan 2010 | ~2002, earlier Nasdaq-100 ETFs |
| S&P 500 | 19 May 2010 | ~2002 via ETFs; earlier via US equity funds |
| MSCI EM IMI | 30 May 2014 | ~2005 via EM ETFs; earlier via EM funds |
| US Energy | 7 Jul 2015 | ~2009, earlier energy sector UCITS ETFs |
| Broad Commodities | 29 Nov 2021 | ~2006–2008, commodity index ETCs |
| Euro inflation-linked bonds | 18 Nov 2005 | 2005 |
| Xetra-Gold | Dec 2007 | always, as physical gold |
| US Treasury 7–10y, EUR-hedged | 25 Jul 2025 (share class) | earlier share classes of the same fund; hedging cost modelled from the US–euro short-rate gap |
| Euro government bonds | 19 Feb 2019 | ~2003 via ETFs; government bonds directly before |
| EUR overnight rate | Mar 2008 | always, via money-market funds |
| USD T-bills 0–1y | 20 Feb 2019 | always, via money-market funds or direct T-bills |

Approximate dates are confirmed source by source in `config/holdings.yaml` (`exposure_available_from`, with source) when the instrument registry is built. Results are reported for the full backtest and from 2010, when every core fund except the newest share classes existed.

### 3.2 Signal universe

Signals draw on a much wider universe than the holdings: several hundred series, none of which is held.

- Equity indices by region, country, sector, size and factor (including US-listed ETFs used only as signals and history proxies, e.g. XLE, XLK, IWM, VGK, EEM).
- Rates, yield curves and credit spreads (US, euro area, BTP–Bund).
- FX, including the dollar index and EUR/USD.
- Commodities: oil, copper, gold, silver, broad indices.
- Volatility indices (VIX and term structure).
- Crypto: daily price, volume and market capitalization for BTC, ETH, the largest coins and the main stablecoins.
- Macro: growth, inflation, labour, credit, financial-conditions and stress indices, used as published at the time.

### 3.3 Feature definitions

Every feature is computed point in time: on date *t* it uses only data published on or before *t*. Windows are in trading days for exchange-traded assets and in calendar days for crypto, which trades every day. Default parameters below are pre-registered; any change is a new trial in the ledger.

| Feature | Definition |
| --- | --- |
| Return | Daily log return r_t = ln(P_t / P_{t-1}) |
| Moving averages | Simple moving averages of close over 7, 21, 50, 100, 200, 300 and 350 days and 2 years (504 trading days; 730 calendar days for crypto) |
| Distance to MA | P_t / MA_t − 1, also expressed as a z-score against its own trailing 3-year history |
| MA touch | The day's range contains the MA: low_t ≤ MA_t ≤ high_t |
| MA held as support | A touch from above after which the close stays above the MA for the next k days (k = 3) |
| MA rejection | A touch from below after which the close stays below the MA for the next k days (k = 3) |
| MA cross | The close moves from one side of the MA to the other |
| Ratios to gold and to the S&P 500 | For every asset, P_asset / P_gold and P_asset / P_S&P500 in a common currency; all features above are also computed on these ratios |
| BTC dominance ex-stablecoins | BTC market cap / (total crypto market cap − market cap of the largest stablecoins). The stablecoin set (e.g. USDT, USDC and the next largest by market cap) is defined point in time. A falling value typically marks euphoric phases |
| Time-series momentum | Cumulative return over 1, 3, 6 and 12 months; the 12-month version skips the latest month (12-1) |
| Risk-adjusted momentum | Momentum divided by realized volatility over the same window |
| Cross-sectional momentum | Rank of each asset's momentum within its group |
| Acceleration | Change in 3-month momentum over the last month |
| Realized volatility | Annualized standard deviation of daily log returns over 21, 63 and 252 days; also EWMA volatility with λ = 0.94 |
| Downside deviation | √(mean of min(r_t − τ, 0)²) over the window, with target τ = 0 |
| Move z-score | h-day change divided by the trailing standard deviation of h-day changes (3-year window), for h = 21, 63, 126 |
| Drawdown | P_t / max(P_s, s ≤ t) − 1, plus time elapsed since the last all-time high |
| Correlation and beta | Rolling 63- and 252-day correlation and beta to the S&P 500; downside beta uses only days when the S&P 500 falls |
| Volatility spillover | Diebold–Yilmaz spillover index from a rolling VAR on asset volatilities |
| Macro features | Level, 3-month annualized change and year-on-year change, lagged to publication date and taken from real-time vintages where available |

**Cross-asset correlation.** Hundreds of assets give tens of thousands of possible pairs, too many to track one by one and a direct route to overfitting. Correlation is therefore measured at the level of groups and of the whole market:

| Feature | Definition |
| --- | --- |
| Group factors | Each asset group (crypto, US equities, international equities, energy, commodities, bonds, gold, cash) is summarized by one equal-weighted return series. Correlations are computed between these ~8 factors, not between individual assets |
| Risk-on / safe-haven correlations | Rolling 21- and 63-day correlation between the crypto and equity factors, and between the equity and bond factors |
| Average pairwise correlation | Mean of all pairwise correlations within the signal universe (rolling 63 days); a rising value means diversification is failing |
| Absorption ratio | Share of total variance explained by the first few principal components of all asset returns (Kritzman et al., 2011); high values mark fragile, tightly coupled markets |
| Joint-drawdown indicator | Share of risky-asset groups simultaneously more than 2 standard deviations below their trailing mean weekly return, and the number of consecutive weeks in that state. For example, crypto and equities falling hard together |
| Correlation regime change | Difference between 21-day and 252-day average correlation, as a z-score |

**Cycle-framing features.** Signals known to frame the phases of the cycle are built explicitly, for the US and the euro area:

| Feature | Definition |
| --- | --- |
| Policy-rate gap | 2-year government yield minus the policy rate (US: Fed funds; euro area: ECB deposit rate). Strongly negative values mean markets price rate cuts, typical late in a cycle |
| Yield-curve slope | 10y − 2y and 10y − 3m spreads, their level and 3-month change |
| Curve inversion and re-steepening | Days since the curve inverted; re-steepening after an inversion, which has historically come close to downturns |
| Blow-off moves | Upside move z-scores of the risk leader above a threshold, plus acceleration of its price (parabolic advance), signalling euphoric late-cycle phases |
| Breadth divergence | The S&P 500 at new highs while equal-weight/cap-weight, S&P 500/gold or BTC dominance ex-stablecoins fall |
| Late-cycle leadership | Relative strength of energy and commodities against the broad market |

Candidate signals proposed during research (for example Nasdaq/BTC and gold/BTC relative strength, BTC realized price, Kronos forecast dispersion) enter through the same protocol as any other idea: pre-registered, logged and tested walk-forward.

### 3.4 Cycle-phase model

The features are combined into a probability for each phase of the market cycle: early, mid, late and downturn (e.g. "80% probability of being near a cycle top").

The target is defined by market behaviour, not by official business-cycle dates. Beyond the few major cycle peaks, markets go through many shorter episodes in which risky assets clearly outperform safer ones, and others in which the reverse holds. Each episode is labelled from the forward relative return of a risky basket against a safe basket over 1, 3 and 6 months. This yields many more events to learn from than official cycle dates and captures the short risk-on and risk-off phases the portfolio aims to exploit.

- Models: logistic regression as the baseline, gradient boosting, and Markov switching (Hamilton, 1989) on returns and volatility.
- Probabilities are calibrated (reliability diagrams; a predicted 80% must come true about 80% of the time).
- Training and selection use purged walk-forward cross-validation with an embargo (López de Prado, 2018).
- **Yearly retraining.** Each January the model is re-fitted on all data available up to that date (expanding window). New assets and signals enter automatically as role members change (section 3.5). A variant that weights recent years more heavily is pre-registered and reported alongside.

### 3.5 Risk ladder

Assets sit on a ladder from riskiest (crypto) through equities and commodities to bonds, gold and cash. As the late-cycle probability rises, risk is cut gradually, riskiest assets first, into assets that historically held up while risky assets fell; risk is added back as probabilities turn. Momentum selects holdings within each rung.

**Signal roles by era.** The holdings and their rungs are fixed. The signals, however, track roles whose members change over time: the asset leading risk appetite in one era (small caps or the Nasdaq in earlier decades) is not the one leading it today (Bitcoin). Roles include *risk leader*, *core equity*, *cyclical/late-cycle*, *inflation hedge* and *safe haven*.

- **Candidates by size at the time.** Each January, the candidate pool consists of the largest and most traded assets of that moment within the signal universe (by market capitalization or trading volume as known then), so assets are never chosen with hindsight.
- **Ranking by risk.** Candidates are ranked by trailing 3-year volatility, downside beta to the S&P 500 and maximum drawdown; the ranking assigns each role's members for that year.
- **Role features.** Features are computed on each role (for example the drawdown or momentum of the current risk leader), so a signal keeps the same meaning across eras even as its members change.
- An asset enters the pool only from the date it had a liquid price history. Long histories extend the roles back to the 1970s (Kenneth French size portfolios from 1926, the Nasdaq Composite from 1971).

**Topping order.** The claim that risky assets top first and energy last is tested, not assumed: an event study around past cycle peaks measures which assets topped first and last, and how consistently. The result is published.

### 3.6 Optimizer

- **Objective:** maximize the portfolio's Sortino ratio after costs and German tax, subject to the two hard requirements in section 1, on development data only.
- **Search space:** cycle-phase thresholds, momentum lookbacks, weight caps per rung, rebalancing bands, rebalancing frequency and the volatility target.
- **Method:** walk-forward evaluation; each candidate is tuned on one period and scored on the next, never on data it was tuned on.
- **Risk models:** hierarchical risk parity and downside-risk weighting within rungs.

### 3.7 Rebalancing and risk checker

- **Rebalancing frequency:** monthly, quarterly and yearly are pre-registered as three variants. Less frequent rebalancing realizes fewer gains and trades less, so it pays less tax and fewer costs, but reacts later. All three are reported.
- **No-trade bands** avoid small trades; tax-aware rules sell loss-making lots first and use new money to rebalance where possible.
- **Daily risk checker:** each day, a strong stress signal can cut risk between rebalances; adding risk back waits for the next scheduled rebalance. A variant without the checker is reported to show its cost in trades and tax, including any broken 12-month holding periods.

### 3.8 Costs

All costs are applied trade by trade. Every parameter sits in `config/costs_taxes.yaml` with its source URL and the date it was checked; a test fails if any entry lacks a source.

| Cost | Rule | Source |
| --- | --- | --- |
| Broker commission | Published schedule of the reference broker (base case Interactive Brokers, fixed pricing) | Broker fee page |
| Bid–ask spread | Half the spread on every trade, estimated from daily highs and lows (Corwin–Schultz, Abdi–Ranaldo) and validated against quote snapshots | Own estimates |
| Fund costs | Each fund's TER, deducted daily; hedging costs are already in hedged share-class returns | Fund factsheets |
| Crypto | Exchange fee schedule by volume tier, spread, and network fees to and from cold storage | Exchange fee pages |
| Market impact | Ignored below a €1m portfolio, stated as an assumption | – |

### 3.9 German tax engine

Lot-level FIFO accounting per asset, wallet and account, in EUR.

**Assumption.** Current German tax law is applied to the whole backtest history, including years in which other rules were in force. After-tax results show how today's rules would have treated each trade, not the tax actually due at the time, and state this wherever they appear (decision 0006).

| Item | Rule in the model |
| --- | --- |
| Capital income | 25% flat tax plus 5.5% solidarity surcharge (26.375%); no church tax |
| Equity funds | 30% partial exemption (Teilfreistellung) on gains and losses |
| Bond, commodity and money-market funds | No partial exemption |
| Accumulating funds | Vorabpauschale each January, credited against the gain on sale |
| Xetra-Gold | Tax-free after 12 months; personal income-tax rate before |
| Crypto held directly | Tax-free after 12 months (§23 EStG); before that, personal income-tax rate with the €1,000 annual exemption threshold |
| Personal income-tax rate | For taxable short-term sales under §23 EStG (crypto and Xetra-Gold held 12 months or less): a parameter in `config/costs_taxes.yaml`, default 42% without solidarity surcharge, sourced to the §32a EStG 2026 tariff |
| Allowance and losses | €1,000 saver's allowance; losses carried forward without limit |

Results after tax are reported two ways: "if sold today" and "still invested", which is fair to buy-and-hold, since it defers almost all of its tax.

### 3.10 Risk, scorecard and Monte Carlo

- Scorecard against the benchmark and the reference strategy for the full sample, each decade and each crisis.
- Risk: downside deviation, maximum drawdown, CVaR, stress scenarios (2008, 2020, 2022, rate shocks), look-through exposures by country and sector from ETF holdings.
- Monte Carlo: regime-conditional block bootstrap of future paths for the portfolio and the benchmark, with fan charts and the probability of a 20% drawdown.

### 3.11 Machine-learning strategies

Machine-learning approaches that search many strategies and refine them (gradient boosting, genetic programming, reinforcement learning) are automated versions of the same search the optimizer runs, and they are governed by the same protocol:

- Each ML approach is pre-registered as one strategy family with a fixed search budget.
- Every configuration evaluated counts as a trial in the ledger, so the deflated Sharpe ratio reflects the true size of the search.
- Training and model selection use purged walk-forward cross-validation with an embargo, on development data only.
- The holdout is never used to choose or tune a model; the model is frozen before the holdout is unsealed.
- An ML model is kept only if it beats the simpler rule-based version out of sample.

---

## 4. Research integrity

When many strategies are tested on the same history, some look good by chance: testing 100 strategies with no real edge yields about 5 that pass a 5% significance test. Four mechanisms make the reported result credible.

| Mechanism | Implementation |
| --- | --- |
| Pre-registration | `research/preregistration.yaml` states the objective, metrics, thresholds and variants before any backtest. The owner writes it; agents may create only an empty template. It is committed and tagged, and a hook blocks agents from editing it. |
| Trial ledger | Every backtest is appended automatically to `research/ledger.parquet`: strategy, parameters, data window, git commit, results. Deflated Sharpe and probability of backtest overfitting use the real trial count. Rejected strategies are published on the Research log page. |
| Holdout vault | Development data ends on 31 December 2022. Data from 1 January 2023 onward is the holdout. It is stored in R2 under a separate, encrypted prefix; `data/holdout/` is its local equivalent and is gitignored. In research mode the point-in-time loader refuses any date after 2022-12-31 unless an explicit unseal flag is set, a locked test fails if a development run touches holdout data, and agent hooks block reads of it. It is unsealed once per release by a script that logs the event. |
| Live record | From launch, each month's target weights are committed with a timestamp before the month starts, so the live record cannot be backfilled. |

**Research and production modes** (decision 0002). Code that reads data runs in one of two modes:

- **Research mode** (backtests, optimization, model selection) refuses any date after 2022-12-31 unless the unseal flag is set.
- **Production mode** (nightly ingestion, data-quality pages and, after the phase 5 freeze, the live weights) may read current data. Before the freeze, post-2022 data is only ingested and stored, never used by any model or backtest.

**Known limitations, stated openly**

- The 2008, 2020 and 2022 crises all fall in the development period; the holdout is mostly a strong bull market, where a defensive portfolio is most likely to lag.
- Crypto has six development years (2017–2022), including the 2018 and 2022 drawdowns.
- An earlier exploratory analysis examined 2000–2026 for the 10-month moving-average rule only; the vault protects all strategies not yet tested.

---

## 5. Data platform

### 5.1 Sources

All sources are free. Licences and download limits are checked when each adapter is built; raw data is never republished, only derived results.

| Domain | Sources | Used for |
| --- | --- | --- |
| Equities and ETFs (Europe, US) | Stooq bulk daily prices; issuer pages for UCITS data | Holding returns, proxies, sector and divergence signals |
| ETF quotes and fees | Exchange data, issuer factsheets, quote snapshots | Spread validation, TERs |
| ETF holdings | Daily holdings files from iShares, Xtrackers, Amundi, SPDR | Look-through risk |
| Fundamentals | SEC EDGAR XBRL company facts | Valuation signals such as earnings yield against bond yields |
| Macro and rates | FRED and ALFRED (vintages), ECB Data Portal, Eurostat, OECD, BIS | Cycle-phase model, rate and stress signals |
| Long histories | Shiller data (S&P 500 since 1871), Kenneth French data library (since 1926) | Benchmark history, dynamic ladder |
| Commodities | World Bank Pink Sheet, FRED, ETC prices | Commodity holdings and signals |
| Crypto | CoinGecko and CoinMetrics community data: daily price, volume, market capitalization; stablecoin market caps | Crypto holdings, BTC dominance ex-stablecoins, crypto signals |
| Volatility | Cboe VIX history | Stress signals |
| Instrument master | OpenFIGI, ISIN and ticker lists | Linking each UCITS ETF to its index, proxy and tax class |

**BTC dominance ex-stablecoins** needs complete histories of total crypto market capitalization and of stablecoin market capitalization. Sources, in order of preference:

1. CoinMarketCap historical snapshots: weekly listings of every coin with its market cap since April 2013, giving total market cap and stablecoin caps from one consistent source.
2. DefiLlama stablecoins API: daily circulating supply of every stablecoin, free and complete since stablecoins emerged.
3. CoinGecko and CoinMetrics community data: daily BTC and large-coin market caps to fill weekly gaps and cross-check.

Each source's terms of use are checked before an adapter is built; where two sources overlap, their difference is measured and reported.

### 5.2 Storage model

- **Bronze:** a manifest of every raw file (URL, checksum, retrieval date), plus stored copies only of data that cannot be downloaded again (e.g. daily ETF holdings snapshots).
- **Silver:** typed, deduplicated, corporate-action adjusted, each value stamped with the date it became known.
- **Gold:** a star schema read by models, the website and the exports: `fact_price_daily`, `fact_crypto_daily`, `fact_holding`, `fact_fundamental`, `fact_macro_vintage`, `fact_feature_daily`, with `dim_instrument`, `dim_date`, `dim_source`.

### 5.3 Quality

dbt tests on every model (unique keys, no gaps, no negative prices, freshness), anomaly checks for jumps and stale prices, and a public data-quality page refreshed nightly. dbt's documentation site, with the lineage graph, is published with the website.

---

## 6. Infrastructure

| Need | Solution |
| --- | --- |
| Compute | GitHub Actions on a public repository (standard runners: 4 CPUs, 16 GB RAM, 14 GB SSD per job) |
| Data lake | Cloudflare R2 (S3-compatible, no egress fees) |
| Nightly pipeline | Scheduled workflow: adapters → Parquet in R2 → dbt with DuckDB reading R2 → features, models, exports → website rebuild |
| Website hosting | Cloudflare Pages, custom domain |
| Budget | Target $1–2 per month (R2 storage beyond the free tier, domain); hard ceiling $15 |

**Constraints and safeguards**

- The nightly pipeline is deterministic code; no language model produces or alters any published number.
- Runners start empty and have limited disk: data is processed one partition at a time, and backfills run as throttled matrix jobs.
- A nightly check fails the build if the bucket exceeds 50 GB.
- Secrets live only in GitHub Actions secrets and a local `.env` file excluded from version control: `FRED_API_KEY`, `R2_ACCOUNT_ID`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`.
- Tests run on small fixture datasets committed to `tests/fixtures`, so every test runs anywhere without credentials.

---

## 7. Outputs

### 7.1 Website

**Pages:** Terminal (landing), Scorecard, Signals and regimes, Portfolio and weights, Costs and taxes, Risk, Research log (trial ledger and rejected strategies), Data (warehouse, tests, lineage), Downloads, Methodology.

**Visuals.** Every visual shows real data and states what it represents.

| Visual | What it shows |
| --- | --- |
| Landing 3D animation | A particle cloud built from the Monte Carlo distribution of the portfolio and the benchmark, spreading into the future and collapsing onto realized values each month |
| Live simulation | Visitors set a horizon and monthly saving; about 100,000 paths re-run on the GPU in the browser (WebGPU) |
| Regime state | Cycle-phase probabilities and current weights, labelled as a visual metaphor |
| Correlation field | Rolling correlations, showing assets moving into phase during crises |
| Optimization landscape | The robustness surface over two parameters: a wide plateau means robust, a lone spike means overfit |
| Trial cloud | Every backtest in the ledger as one particle, placed by return and downside deviation, with the chosen strategy and the benchmark highlighted |
| SQL console | Visitors query a sample of the gold tables in the browser (DuckDB-WASM) |

**Design direction.** Minimal and modern, near-black with a light mode for reading pages. References:

| Reference | Elements adopted |
| --- | --- |
| raycast.com | Landing page, animation, header bar, buttons, font, custom graphics |
| modal.com | Custom 3D animation on the landing page, buttons, font, animated graphics (neutral palette instead of its green accent) |
| resend.com | Custom animated graphics, font, cards |
| attio.com | Simplicity, scrolling structure and scroll animations, custom graphics |
| warp.dev | Simplicity, animations, section divisions |
| huly.io | Near-black interface, lighting effects in the hero, feature sections pairing product views with compact icon grids |

At most one accent colour, used only to carry meaning (for example the regime state). A design system (colours, type, spacing, components, chart rules) is defined once in `DESIGN.md` and the `web/ui` component library, and every page, chart and export uses it.

**Design workflow.**

1. **Reference analysis.** Screenshots of each reference site are analysed for typography, spacing, colour, motion and layout; the chosen elements are written into `DESIGN.md`, which `CLAUDE.md` imports so every page follows it.
2. **Design skills.** Building uses established open-source design skills for Claude Code: Anthropic's `frontend-design` skill and `taste-skill` during construction, and `impeccable` for refinement passes (`polish`, `distill`, `bolder`). Each skill is installed from its repository and pinned to a version.
3. **Motion.** Animations follow interface-motion principles in the style of Emil Kowalski's work (short durations, natural easing, purposeful transitions, respect for reduced-motion settings).
4. **Verification.** The `preship-qa` workflow opens every page in a browser at 390 px and 1440 px, checks loading, empty, error and data states, and records before-and-after screenshots.

**Footer.** A compact integrity strip at the end of every page: rows in the warehouse, data tests passing in the latest run, live record against the benchmark, trials in the ledger, and a link to the latest referee report.

### 7.2 Automated reports

Both are generated by the pipeline after each monthly rebalance, with no manual steps, and published on the Downloads page.

| Output | Content |
| --- | --- |
| Excel workbook | Weights, trades, tax lots, risk figures, and a sheet that recomputes the month's German tax with live formulas and matches the Python engine |
| PowerPoint report | What changed in the weights and why, performance against the benchmark, risk and costs. It uses the website's design system: same palette, fonts, chart style and layout grid |

---

## 8. Development workflow

The codebase is built with Claude Code. Agents run only when started by a developer, either in an interactive session or through a manually triggered GitHub Actions workflow; nothing agent-driven runs on a schedule.

### 8.1 Repository rules (CLAUDE.md)

- Every silver table carries a `known_at` column. Python reads data only through `pit.get(series, as_of=date)` in `src/tiferet/data/pit.py`; SQL features and dbt gold models filter on `known_at <= as_of`. Raw ingestion by the adapters in `src/tiferet/data/` is exempt; any other direct read of raw files is a bug. The locked look-ahead test covers SQL features too (decisions 0003, 0005).
- Never read the holdout, in R2 or in `data/holdout/`; research mode never requests a date after 2022-12-31 (decision 0002).
- Protected files: the owner writes `research/preregistration.yaml`; agents may create only an empty template of it. Agents may draft tests in `tests/locked/`, which the owner approves before they are tagged. After tagging, no one edits either.
- Every backtest goes through `run_backtest()` in `src/tiferet/research/`, which writes to the ledger (decision 0005).
- Every cost or tax number, including each fund's TER, lives in `config/costs_taxes.yaml` with a source URL and a date checked (decision 0004).
- Definition of done: `uv run pytest` green; `ruff check` and `ruff format --check` clean; `mypy --strict` clean on `src` and `tests`; ledger updated for tasks that run a backtest; referee report written for tasks that produce a published result.
- **Publication hygiene.** Everything tracked by git is written for an external reader. No conversational text, personal notes, notes addressed to the repository owner, or exploratory leftovers in code, comments, docs, commit messages or pull-request descriptions. Working notes, scratch analyses and messages for the owner go in `notes/`, which is excluded from version control.

### 8.2 Hooks

All hooks are Python scripts, so they behave the same on Windows and on Linux runners.

| Hook | Event | Behaviour |
| --- | --- | --- |
| `vault_guard.py` | PreToolUse (Read, Grep, Glob, Bash) | Blocks any access to `data/holdout` |
| `prereg_guard.py` | PreToolUse (Edit, Write) | Blocks edits to the pre-registration (except creating its empty template) and to tagged locked tests |
| `lint_on_edit.py` | PostToolUse (Edit, Write) | Runs `ruff format` and `ruff check --fix` on the changed file |
| `test_gate.py` | Stop | Runs the fast test suite (`pytest -m "not slow"`) and blocks completion while tests fail; checks `stop_hook_active` to avoid loops |

**Rules are enforced in code first, agent configuration second.** Hooks and `permissions.deny` rules in `.claude/settings.json` (for example denying `git push` and edits to the pre-registration) apply to local sessions only; cloud sessions do not apply them. The binding protections therefore live in the codebase and CI, where they hold for every session and contributor:

- the point-in-time loader's holdout refusal and the locked no-look-ahead test;
- a CI check that fails if `research/preregistration.yaml` or `tests/locked/` change after their tagged commit;
- a CI check that fails if any `config/` entry lacks a source;
- encryption of the holdout, with the key kept outside the repository.

### 8.3 Agents

| Agent | Model | Access | Responsibility |
| --- | --- | --- | --- |
| builder | Sonnet | Edit | Implements code from an agreed plan |
| optimizer | Opus | Edit (research code) | Proposes strategies and parameters inside the pre-registered search space and runs walk-forward backtests on development data |
| quant-referee | Opus | Read-only | Tests each result for look-ahead, overfitting, cost sensitivity (costs ×2), parameter sensitivity, decade-by-decade stability and the reference strategy |
| tax-auditor | Opus | Read-only | Checks each tax rule against its legal source and worked examples |
| data-auditor | Haiku | Read-only | Checks data quality, gaps and publication dates |
| fixer | Sonnet | Edit | Applies reviewers' findings; the only agent that edits after a review |

### 8.4 Workflows and skills

- **Workflows** (`.claude/workflows/`): `research-cycle` (hypothesis → implementation → development-data backtest → ledger → referee and data audit → independent verification of each finding → verdict; stops after two rounds without progress), `tax-audit`, `preship-qa` (design consistency, mobile, all states, user flows, launch checks).
- **Project skills** (`.claude/skills/`): `add-signal`, `replicate-strategy`, `tax-rule`, `monthly-report`.
- **External design skills:** `frontend-design`, `taste-skill` and `impeccable` (section 7.1), used only for work in `web/`.
- Every change is reviewed with `/code-review` before merge (`ultra` at phase gates).

### 8.5 Task contracts, state and change receipts

- **Task contract.** Every agent task starts from a short contract: objective, scope (files and modules it may change), constraints (what must not change), acceptance checks (the exact commands and tests that prove completion) and actions requiring human approval (pushing, unsealing the holdout, changing the pre-registration). Larger tasks keep their contract in the pull-request description.
- **Context map.** `CLAUDE.md` holds a compact map of the repository (where data, features, models, portfolio and tests live) instead of full documentation; details are read on demand.
- **Durable state.** Decisions and their reasons are recorded as short decision records in `docs/decisions/`, so later sessions do not reopen settled questions. Facts discovered about the environment and lessons from failures that should change future behaviour are added to `CLAUDE.md` when they are general, and kept in `notes/` when they are personal working notes.
- **Evidence of completion.** A task is complete only when its acceptance checks pass; the Stop hook and CI enforce this.
- **Independent verification.** Reviews run in a fresh context with an explicit rejection rubric (look-ahead, survivorship, tax-lot handling, costs, overfitting); the reviewer tries to break the result rather than confirm it.
- **Change receipt.** Each run ends with a receipt in the pull-request description, or in the session's final message when there is no pull request: objective, what changed, what was verified (commands and results), what was not verified, decisions taken, open risks, and approvals needed. Backtest runs also write their receipt to the trial ledger.
- **Minimal structure.** Additional agents, tools or orchestration are added only when a recorded failure shows they are needed.

### 8.6 Model and effort guidelines

| Task | Model | Effort |
| --- | --- | --- |
| Phase planning, architecture, pre-registration review | Opus | high |
| Tax rules, cost model, look-ahead checks | Opus | medium, step by step |
| Implementation from an agreed plan | Sonnet, with Opus as advisor on long builds | medium |
| File scanning, data checks, documentation lookups | Haiku | low |
| Referee and audit reviews, including the tax-auditor | Opus | high |
| Large multi-file builds at the start of a phase | Opus with ultracode | as needed |

Sessions start fresh for unrelated tasks, plans precede implementation, `CLAUDE.md` stays under 200 lines, and exploration runs in subagents to keep the main context small.

---

## 9. Repository structure

```text
tiferet/
  CLAUDE.md                # agent rules and definition of done
  REVIEW.md                # review checklist
  DESIGN.md                # design system
  README.md                # headline result, how to run
  pyproject.toml           # uv-managed, pinned dependencies
  docs/
    plan.md                # this specification
    decisions/             # short decision records
  config/
    holdings.yaml          # instrument identity: ISIN, tax class, launch date, exposure date, sources
    costs_taxes.yaml       # every fee (TER included) and tax parameter, with source and date checked
  research/
    preregistration.yaml   # hook-protected
    ledger.parquet         # every backtest
    referee/               # one report per published result
  src/tiferet/
    data/                  # one adapter per source; point-in-time accessor in pit.py
    features/              # feature library (SQL in DuckDB + Python)
    models/                # cycle-phase models, Markov switching, spillover
    portfolio/             # ladder, optimizer, costs, tax engine, scorecard, Monte Carlo
    research/              # run_backtest() and ledger writer (I/O allowed)
    reports/               # Excel, PowerPoint, website data exports
  dbt/                     # bronze, silver, gold models and tests
  web/                     # website (React, Three.js, design system in web/ui)
  tests/
    fixtures/              # small datasets for credential-free tests
    locked/                # hook-protected tests (no look-ahead)
  .claude/
    settings.json          # hooks
    agents/  workflows/  skills/  hooks/
  .github/workflows/       # ci.yml, nightly.yml, agent jobs (manual trigger)
  data/                    # local data (gitignored); data/holdout/ is the local holdout
  notes/                   # untracked working notes (gitignored)
```

---

## 10. Code standards

1. **Look-ahead is impossible by construction.** A single point-in-time accessor stores each observation with its publication date and raises on any request beyond `as_of`. A locked test feeds a synthetic series with a known future spike and fails if any feature reacts early.
2. **Pure core.** Features, weights, costs and tax are pure functions; I/O lives only in adapters, the pipeline and `src/tiferet/research/` (backtest runner and ledger writer).
3. **Typed and validated.** `mypy --strict`, frozen dataclasses for configuration, `pandera` schemas on every DataFrame crossing a module boundary.
4. **Exact money.** Tax and lot accounting use `Decimal`.
5. **Tests that pin the money.** Property tests for invariants (no negative tax after the loss pot, partial exemption never above 30%, no sale exceeding units held), golden tests from worked tax examples, snapshot tests on the scorecard.
6. **Sourced configuration.** Every fee and rate has a source and a check date; a test enforces it.
7. **Reproducibility.** `uv.lock`, fixed random seeds, and every ledger row records the git commit and data snapshot hash.
8. **Small modules.** Files under ~300 lines, functions under ~40.

---

## 11. Roadmap

| Phase | Scope | Gate |
| --- | --- | --- |
| 1. Warehouse core | Repository harness (rules, hooks, agents); point-in-time layer; adapters for macro, equities, ETFs, commodities, crypto; bronze/silver/gold with dbt tests; nightly pipeline; minimal site | Nightly build green for seven consecutive days |
| 2. Core engine | Feature library, cycle-phase model, cost model, German tax engine; benchmark and reference strategy after tax; first scorecard online | Tax worked examples pass; site v0.1 |
| 3. Cost model calibration | Spread estimates from daily data, validated on quote snapshots; costs re-applied to all benchmarks | Every cost parameter sourced from data or published schedules |
| 4. Research loop | Pre-registration, trial ledger, holdout vault, research-cycle workflow; optimizer and ML families on development data | Deflated Sharpe uses the ledger count; strategy frozen |
| 5. Verdict and live record | Holdout unsealed once; verdict published whatever the outcome; live record starts; automated Excel and PowerPoint reports | v1.0 tagged |
| 6. Website | Design system, landing 3D animation, live simulation, all pages, integrity footer, pre-ship QA | Pre-ship QA clean; public launch |

---

## 12. References

- Abdi, F. and Ranaldo, A. (2017). A simple estimation of bid-ask spreads from daily close, high, and low prices.
- Bailey, D. and López de Prado, M. (2014). The deflated Sharpe ratio.
- Corwin, S. and Schultz, P. (2012). A simple way to estimate bid-ask spreads from daily high and low prices.
- Diebold, F. and Yilmaz, K. (2012). Better to give than to receive: predictive directional measurement of volatility spillovers.
- Faber, M. (2007). A quantitative approach to tactical asset allocation.
- Hamilton, J. (1989). A new approach to the economic analysis of nonstationary time series and the business cycle.
- Harvey, C., Liu, Y. and Zhu, H. (2016). … and the cross-section of expected returns.
- Kritzman, M., Li, Y., Page, S. and Rigobon, R. (2011). Principal components as a measure of systemic risk.
- Ledoit, O. and Wolf, M. (2008). Robust performance hypothesis testing with the Sharpe ratio.
- López de Prado, M. (2018). Advances in financial machine learning.
- Moskowitz, T., Ooi, Y. H. and Pedersen, L. H. (2012). Time series momentum.
- Phillips, P., Shi, S. and Yu, J. (2015). Testing for multiple bubbles.
- Sortino, F. and van der Meer, R. (1991). Downside risk.

*Research and education only; not investment advice.*
