# Kalshi project: governing plan

Owner: Jackson (lamouro). Started 2026-11-05. This file governs the project. Every other doc, script and test answers to it. When reality changes, update the plan first.

## 1. Goal
Find a Kalshi strategy, or a combination of strategies, that makes money net of fees on fills a real account would actually have gotten. Prove it in paper mode. Only then trade it live, with a small capped stake.

## 2. Standing rules (non-negotiable)
1. **Paper first, always.** No real-money order of any size without Jackson's explicit go-ahead in writing. That includes calibration probes.
2. **Live cap.** Once live trading is approved, the hard maximum loss is **$100**. A risk monitor runs separately from the strategy, reads the Kalshi account directly and enforces the cap.
3. **Price only on executable books.** No fill on the book the signal saw, the mid, a candle close or a model price. This "phantom book" error turned +14.3c/trade into −18.37c (Paper V).
4. **A resting order needs a trade to fill.** A queue position ahead of it must be cleared by trades. Cancels can move it forward but never fill it (dove `fill_sim.py`).
5. **Fees are exact.** Per-series fee type and multiplier, computed in Decimal and rounded the way Kalshi rounds.
6. **Count every variant searched.** Every strategy, parameter set and combination goes in `results/search_ledger.csv`. A strategy is promoted only after a multiple-testing correction across the whole family: Holm for go-live decisions, Benjamini–Yekutieli at q = 0.10 for promotion to forward paper testing.
7. **Never tune on out-of-sample data.** A failed OOS window is burned; roll forward to the next one.
8. **No stop-losses on binaries.** Refuse the entry instead.
9. **No credentials in git.** Kalshi keys live only in environment variables on the run host.
10. **Report brackets until calibrated.** Resting-order results carry a floor (trades-only queue model) and a ceiling (proportional cancels). A strategy counts only if **both ends** are positive.

## 3. What we already know (summary; details in `/mnt/project-files/kalshi-research-base/`)
- **Past programme:** 7 systems, about 2,500 real trades, roughly flat. Every taker strategy failed on spread plus fee.
- **Market structure:** makers earn about +1.12% per trade gross and takers lose the same (Becker, 72.1M trades). The gap is largest in Media, World Events, Entertainment and Mentions.
- **Wave 2 verdicts.** In the evidence available so far, no single hypothesis shows an edge.
  - Still alive:
    - pre-event maker quoting in mention and slow-culture markets;
    - 95–98c crypto maker bids (desk test only);
    - first maker in newly listed markets.
  - Killed:
    - taker Yes-fade;
    - 85–94c crypto maker bids;
    - Polymarket lead as a taker strategy;
    - LIP farming;
    - stop-losses.
- **The binding constraint is sample size.** n ≈ 6,147 / edge² (edge in cents) at 50% power, or about 12,558 / edge² at 80% power. Paper mode has no collateral limit, so it can test across every eligible market at once.

## 4. Architecture (spec: `docs/PAPER-ENGINE-SPEC.md`)
There is one codebase, and a strategy is a pure function `on_event(event, ctx) -> [Command]`. Only the gateway is swapped between modes:
- **ReplayGateway:** KROS queue simulator on recorded or historical tape.
- **ShadowGateway:** KROS on the live stream, in real time.
- **LiveGateway:** Kalshi V2 `/portfolio/events/orders`. Disabled until Jackson approves.

Shared components:
- Fee model.
- L2 book builder with seq/clock checks and dirty flags.
- Event-sourced ledger: Decimal amounts at $0.0001, reconciled nightly.
- Risk monitor.
- Recorder.

## 5. Data sources, in order of fidelity
1. **Own recorder:** message-level `orderbook_delta` plus trades, through the WS feed (needs an API key and an always-on host). This is the only source for true queue modelling, and it starts as soon as a host exists.
2. **Kalshi public REST history:** `/markets/trades` (the full historical trade tape, with taker side) and minute candlesticks (bid/ask OHLC). Supports trade-through and touch fill brackets, but not exact queue position.
3. **Repo datasets:** `razorbillaws/kalshi_candles.jsonl`, the kalshi-research aggregates, and the dove MBO methodology.
4. **Research base:** literature numbers, used for priors only and never as backtest input.

Hosting status (2026-11-05): no AWS access from the agent session yet. Historical pulls run through a Vercel sandbox on Jackson's account. The always-on recorder still needs an AWS box with an SSH key, or another always-on host.

## 6. Phases and exit gates
| Phase | Work | Exit gate |
|---|---|---|
| P0 | Plan, repo scaffold, research base imported | This file merged |
| P1 | Data: historical trades and candles for target series, plus the recorder code | Datasets on disk, recorder runs against the live feed for 1 h with no gap it cannot explain |
| P2 | Engine: fees, book, KROS, ledger, gateways, risk | Acceptance tests 1–19 in `docs/PAPER-ENGINE-SPEC.md` §7 pass. Planted-edge test recovers a known edge |
| P3 | Strategy library, single strategies paper-tested on history | Each strategy has a bracket result with a day-clustered CI, logged in the search ledger |
| P4 | Combinations: portfolios and ensembles of strategies, gated by regime or category | Holm-corrected across all variants tried, with a walk-forward split (train / validate / burned OOS) |
| P5 | Shadow paper on the live stream for 2+ weeks | Shadow P&L sits inside the historical bracket, and the no-information floor is ≤ 0 |
| P6 | (Jackson-gated) calibration probes, then capped live | Probe fill agreement within 0.05 on the matched stratum, Jackson's written go-ahead, $100 cap armed |

## 7. Strategy universe (P3) and combination method (P4)
Single strategies are listed in `strategies/README.md` as they are built. Initial set:
- **S1 Pre-event mention maker:** passive sell-Yes, plus optional buy-Yes, on mention strikes, pulled at T−15 min.
- **S2 Slow-culture two-sided maker:** fee-free Entertainment and World markets with wide spreads.
- **S3 Crypto favourite maker bids:** 95–98c, KXBTC15M and KXBTCD.
- **S4 Genesis maker:** quote first in newly listed markets with no designated maker.
- **S5 Longshot fade as maker:** sell Yes under 10c, where takers lose more than 60%.
- **S6 Favourite–longshot taker controls:** expected negative; serves as the no-information and sanity floor.
- **S7 Calibration or base-rate tilt:** shift maker quotes toward a base-rate prior, without taking.
- **S8 Late-convergence maker:** rest at the near-certain side in the last minutes before close.

**Combinations.** Combinations are built from the per-strategy daily P&L streams. Methods:
- equal-weight portfolios of every subset of surviving singles;
- inverse-variance weights;
- category and regime gates (only quote S1 when spread ≥ k);
- signal stacking, where one strategy's quote is skewed by another's signal.

Every combination counts as a variant in the ledger. Weights are fitted on the train window only.

## 8. Status log
- 2026-11-05: plan written, repo created. Wave 1 and wave 2 research done (74 verified lanes).
- 2026-11-05 (overnight): wave 3 research (5 lanes, verified). Data path built without a server: public
  REST via WebFetch agents (400 calls/h budget), 13 series market lists, ~480 trade-tape windows
  (64 events) in `/mnt/project-files/kalshi-data/`. Paper engine v0 (tape-touch fills, floor/ceiling
  bracket, fee model incl. direct-member rounding) + 24 tests on branch `engine-v0`.
- Crypto candles: 101 variants, 1,985 markets, none survive Holm; two-sided MM -2 to -3.5c/contract.
- Mentions: 306 variants x PRE/OPEN windows; 575 train-selected combinations scored on test, 0 pass
  Holm or BY. Taker and random controls negative. Flatten-before-go-live does not rescue MM.
- Pitfall: top-volume strike sampling biases toward Yes. Slice 5 drew strikes at random.
- Lead (unconfirmed): Yes-fade maker on random Trump-mention strikes, +5.9c/contract floor
  (SE 2.75, 20 events, train +5.1 / test +7.3). Pre-registered as H-W3-1
  (`results/PREREG_H_W3_1.md`); holdout slice 6 (16 unseen events): NOT confirmed, floor -2.9c (SE 3.6, p 0.78). No strategy has a confirmed edge.
- Blockers: no always-on host (AWS needs an SSH key), sandbox cannot reach Kalshi directly.
