# Review checklist

Used for every review, including `/code-review` before merge (plan §8.4). The reviewer works in a fresh context and tries to break the change rather than confirm it (§8.5). Each unticked item is a finding; a finding under the rejection rubric rejects the change.

## 1. Code standards (§10)

- [ ] **Look-ahead impossible by construction.** Python reads data only through `pit.get`, which raises on any request beyond `as_of`; SQL features and dbt gold models filter on `known_at <= as_of`. Only raw ingestion bypasses this. (§10.1, §8.1, 0003)
- [ ] **Locked look-ahead test intact.** The synthetic future-spike test in `tests/locked/` is unchanged, covers Python and SQL features, and passes. (§10.1, §8.2, 0003)
- [ ] **Pure core.** Features, weights, costs and tax are pure functions. I/O happens only in adapters, the pipeline and `src/tiferet/research/`. (§10.2, 0005)
- [ ] **Typed.** `mypy --strict` is clean on `src` and `tests`. Configuration uses frozen dataclasses. (§10.3, §8.1)
- [ ] **Validated.** Every DataFrame crossing a module boundary has a `pandera` schema. (§10.3)
- [ ] **Exact money.** Tax and lot accounting use `Decimal`. (§10.4)
- [ ] **Tests pin the money.** Money logic has property tests for its invariants (no negative tax after the loss pot, partial exemption never above 30%, no sale exceeding units held), golden tests from worked tax examples, and snapshot tests on the scorecard. (§10.5)
- [ ] **Sourced configuration.** Every fee (TER included) and tax parameter is in `config/costs_taxes.yaml` with a source and a check date, and the test that enforces it passes. `config/holdings.yaml` holds no fees. (§10.6, §3.8, 0004)
- [ ] **Reproducible.** Dependencies are locked in `uv.lock`, random seeds are fixed, and every ledger row records the git commit and data snapshot hash. (§10.7)
- [ ] **Small modules.** Files stay under ~300 lines and functions under ~40. (§10.8)

## 2. Rejection rubric (§8.5)

### Look-ahead

- [ ] Every input used on date *t* was known on or before *t*; macro series are lagged to publication date and use real-time vintages where available. (§3.3, 0003)
- [ ] Rolling windows, z-scores and principal components use trailing data only. (§3.3)
- [ ] Labels built from forward returns never reach the features; cross-validation is purged walk-forward with an embargo. (§3.4)
- [ ] Yearly retraining uses only data available up to that January. (§3.4)
- [ ] Sets that change over time (stablecoins, role members, candidate pools) are defined point in time. (§3.3, §3.5)

### Survivorship

- [ ] Candidate pools are the largest and most traded assets as known at the time, never chosen with hindsight. (§3.5)
- [ ] An asset enters the signal pool only from the date it had a liquid price history. (§3.5)
- [ ] A holding is held only from its `exposure_available_from` date; before that its weight goes to the remaining holdings. (§3.1.1)
- [ ] Proxy returns before a fund's launch subtract the listed fund's costs, and the proxy–fund tracking gap is reported. (§3.1.1)

### Tax lots

- [ ] Lots are FIFO per asset, wallet and account, in EUR. (§3.9)
- [ ] Current German tax law is applied to the whole history, and results state this assumption. (§3.9, 0006)
- [ ] The 30% partial exemption applies to equity funds only, on gains and losses. (§3.9)
- [ ] The Vorabpauschale is charged each January and credited against the gain on sale. (§3.9)
- [ ] Crypto lots are held at least 12 months, enforced per lot. Taxable short-term sales of crypto and Xetra-Gold use the personal income-tax rate parameter from `config/costs_taxes.yaml`. (§3.1, §3.9, 0006)
- [ ] The saver's allowance and unlimited loss carry-forward are applied. (§3.9)
- [ ] The benchmark is taxed under the same rules, and after-tax results are shown both "if sold today" and "still invested". (§1, §3.9)

### Costs

- [ ] Every trade pays commission and half the spread; TER is deducted daily; crypto pays exchange, spread and network fees. (§3.8)
- [ ] The same costs are applied to the benchmark and the reference strategy. (§1, §11 phase 3)
- [ ] The result survives doubled costs. (§8.3)
- [ ] The market-impact assumption (ignored below a €1m portfolio) is stated. (§3.8)

### Overfitting

- [ ] The run went through `run_backtest()` and every configuration evaluated is a trial in the ledger. (§4, §3.11)
- [ ] Parameters stay inside the pre-registered search space; any change to a default parameter is logged as a new trial. (§3.3, §3.6)
- [ ] Each candidate is tuned on one period and scored on the next, in research mode on development data only; nothing reads data after 2022-12-31. (§3.6, §4, 0002)
- [ ] Deflated Sharpe ratio and probability of backtest overfitting use the real trial count. (§4)
- [ ] Results are robust to parameter changes (a plateau, not a lone spike) and stable decade by decade. (§7.1, §8.3)
- [ ] The 10-month moving-average reference strategy is reported alongside. (§1, §8.3)
- [ ] An ML model is kept only if it beats the simpler rule-based version out of sample. (§3.11)

## 3. Before merge (§8.1, §8.5)

- [ ] The definition of done in `CLAUDE.md` holds: tests green, `ruff check`, `ruff format --check` and `mypy --strict` clean; ledger and referee report where they apply.
- [ ] `research/preregistration.yaml` and tagged files in `tests/locked/` are unchanged; any new locked test is a draft awaiting owner approval.
- [ ] No language model produced or altered a published number. (§6)
- [ ] Publication hygiene: no conversational text, personal notes, notes addressed to the owner, or exploratory leftovers in code, comments, docs, commit messages or the pull-request description. Nothing under `notes/` is tracked.
- [ ] The pull-request description contains the change receipt and, for larger tasks, the task contract.
