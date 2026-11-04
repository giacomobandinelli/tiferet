# 0017. The BTC-to-stablecoin ratio replaces BTC dominance ex-stablecoins

- Status: Accepted
- Date: 2026-11-04

## Context

Plan §3.3 defined "BTC dominance ex-stablecoins" as BTC market cap / (total crypto market cap − market cap of the largest stablecoins). It needs a complete history of total crypto market capitalization.

No free, licensed source of that history exists (checked by the owner on 2026-11-04):

- CoinGecko's global market cap chart requires the Analyst plan, $129 a month.
- CoinMarketCap's global metrics from 2013 require the Startup plan, $79 a month.
- CoinMarketCap's website terms forbid scraping, which rules out its historical snapshot pages, the source the plan had preferred.

Coin Metrics Community data covers only the assets it tracks, so summing its market caps would understate the total by a varying, unknown share. The free sources do provide BTC market cap (Coin Metrics) and the market caps of individual stablecoins (DefiLlama).

## Decision

**Definition.** The BTC-to-stablecoin ratio is BTC market cap / (USDT market cap + USDC market cap), on daily decision dates:

- Each input is used only once it was known: it is placed on decision dates by `align`, from its `known_at` (decision 0010).
- A stablecoin enters the sum from its first available value. Before USDC exists, or before its first value is known, the sum is USDT alone.
- The number of stablecoins in the sum is recorded with the ratio on each date, so the step when USDC enters is visible and can be controlled for.
- The ratio starts when BTC and at least one stablecoin are available. It is missing on any date without them.

**Inputs.** BTC market cap is `cm_btc_cap_usd` (Coin Metrics Community). USDT and USDC market caps are `defillama_usdt_cap_usd` and `defillama_usdc_cap_usd` (DefiLlama).

**Implementation.**

- The feature `btc_stablecoin_ratio` is registered in `src/tiferet/features/crypto.py` and is covered by the locked look-ahead test.
- `ratio_and_count` returns the ratio together with the stablecoin count.
- The count is not registered as a feature of its own. It does not respond to the inputs' values, and the locked look-ahead test requires every registered feature to respond to a change in its inputs.

**Interpretation.** The plan described a falling dominance as typical of euphoric phases. The ratio is a different quantity, so no such reading is carried over: its relation to cycle phases is tested, not assumed, like the topping order of §3.5.

## Consequences

Limits of the ratio:

- **Only USDT and USDC.** Other stablecoins (DAI, BUSD, TUSD and others) are left out, so the denominator understates total stablecoin supply, by more in the periods when they were large.
- **USDC starts in 2018.** Its entry steps the denominator up, which the recorded count makes visible. USDT dominates the sum before then.
- **Values are taken at the peg.** DefiLlama's circulating amounts are in pegged US dollars, not at market prices. A depeg is not reflected.
- **Stablecoin supply measures demand for on-chain dollars, not total crypto value.** The ratio relates BTC's value to the dollars parked in crypto, which is a different question from BTC's share of the whole market.

Other consequences:

- Plan §3.2, §3.3 and §5.1 are updated to match. The CoinMarketCap historical snapshots are removed as a source, and CoinGecko is deferred to a later nightly cross-check.
- If a free, licensed total-market-cap history appears, dominance can be added as a further feature. Under the pre-registration rules it is a new trial; it does not replace this ratio.
