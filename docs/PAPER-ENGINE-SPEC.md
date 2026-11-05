# Kalshi paper engine: build spec and acceptance gate (v0, 2026-11-05)

This spec is compiled from the six wave-2 simulator lanes (`notes/w2-sim-*.md`) and their verifier reports. Each section names its source note, and the verifier corrections are already applied. The goal comes from Jackson: paper trading must be the most accurate model of live fills that can be built, and no strategy is trusted until it is. **Until the acceptance gate in §7 passes, no paper number counts as evidence.**

## 1. Why the old paper modes cannot be reused

Every past simulator overstated P&L in the same way (`w2-sim-past-errors`). It priced the trade against a quote that was never executable (the captured book, the mid, a model price, a candle ask, or a $1.00/$0.01 empty-book sentinel) and then filled the whole order instantly.

- Paper V measured the cost: **+14.3c per trade on the captured book became −18.37c on the book that was fillable at fire time.** Only 28 of 245 trades still had a fillable quote.
- Nuthatch, Oriole and Razorbill paper modes fill at the limit with no depth check. RazorbillAWS adds a flat +1c, yet live entries ran 3–7c worse.
- The capstone harness still fills a resting order in full on one 1-lot print of the wrong aggressor side, charges maker fills the taker fee, and has no latency. Its look-ahead test passes vacuously.
- The legacy order endpoint every bot used now returns "Please switch to the V2 endpoints" (deprecated 2026-06-18 to 06-25). No past bot can place an order as written (`w2-sim-v2-orders-port`).

## 2. Architecture: one system, three swappable gateways

Source: `w2-sim-architecture`. The pattern is copied from dove's BrokerAdapter. NautilusTrader's Kalshi adapter is still listed as "building", so there is nothing ready-made to adopt.

```
Kalshi WS ─► RECORDER ─► raw log (verbatim frames, zstd JSONL per market-hour)
                │
           BOOK BUILDER (snapshot + deltas, checked by seq and clock; DIRTY flags)
                │ events
           STRATEGY  on_event(evt, ctx) -> [Command]    pure: no I/O, no wall-clock reads
                │ commands
           RISK PRE-TRADE (size caps, price grid, dirty-state refusal, budget)
                │
           GATEWAY ── ReplayGateway (KROS on recorded tape)
                   ── ShadowGateway (KROS on the live stream, real time)
                   ── LiveGateway   (Kalshi V2 REST + private WS)
                │ order events
           LEDGER (Decimal, $0.0001)  ─►  RECONCILER (nightly live↔ledger; per-order live↔phantom)
           RISK MONITOR (separate process; reads the Kalshi account; can only cancel or halt)
```

- **The strategy code is identical in paper and live.** Only the gateway changes.
- **Event sourcing.** There are three append-only logs: raw exchange frames; own commands and order events (with `mode`, `client_order_id`, `order_id`, `exchange_index`); and settlement and account events. Book, positions and cash are folds over those logs. Replaying the logs from empty must reproduce the ledger bit-for-bit.
- **Idempotent submission.** Every command carries a strategy-generated `client_order_id`. This prevents the 2026-04-15 RazorbillAWS duplicate-order bug ("bought 8 contracts on one signal").
- **Look-ahead guard.** Carry over the harness's `MarketSnapshot.__post_init__` check, extend it to depth ladders and extra fields, and give it a test that builds a real object.

## 3. Recorder (start this first; history cannot be bought)

Source: `w2-sim-kalshi-data-feed` (verifier: solid).

**Subscriptions**
- `orderbook_delta` in real time, never conflated, with `use_yes_price` logged explicitly.
- `trade` with no market filter (the whole venue tape).
- `market_lifecycle_v2`.
- `ticker` as a best-bid/offer cross-check.
- `user_orders`, `fill` and `market_positions` on the account.
- The reference feed for the underlying (e.g. `cfbenchmarks_value_5hz` for crypto).

**Per message**
- Store the raw bytes plus `conn_id`, `recv_mono_ns` and `recv_wall_ns` (chrony-disciplined).
- Store the parsed `type, sid, seq, clock, ts_ms, sending_ts_ms, market_ticker`.
- Never store parsed fields alone. Kalshi changes schemas often: `clock` was added on 2026-10-29 and `taker_side` was replaced on 2026-05-06.

**Integrity**
- Check `seq` contiguity per subscription. On any gap, call `get_snapshot`, write a `gaps` row and mark the market DIRTY. The simulator refuses to trade inside a dirty window.
- Take a checksum snapshot every 5 minutes per market and compare it level by level.
- After the Thursday 03:00–05:00 ET maintenance window, start a new clock epoch.
- Run two redundant connections, ideally on two hosts, merged by `(market, clock, index)`. This is valid only for real-time streams.
- The read loop only receives, timestamps and enqueues. Parsing happens elsewhere.
- Use an off-box heartbeat, disk alarms and an S3 backup. The past collectors died for 16 hours once and 76 days another time.

**Daily REST sidecar**
- Market metadata: rules, `price_ranges`, close times.
- `/markets/trades`, reconciled against the WS tape.
- `/portfolio/fills` and orders.
- Exchange schedule.

**Size and cost:** about 330 B per raw delta. A thin market is under 1 MB per day compressed; a hot 15-minute crypto market is about 22–41 MB per day. An always-on AWS box in us-east-2 costs about $12–19 a month, or $20–40 with a redundant recorder.

## 4. KROS v0: the Kalshi Resting-Order Simulator

Source: `w2-sim-fill-models` (fill model) and `w2-sim-demo-env` (calibration).

**Canonical book.** Everything maps to a single YES-price book on the market's `price_ranges` grid.
- YES bids at `p` form the bid ladder.
- A NO bid at `q` is a YES ask at `1 − q`. So "buy NO at 40c" is the V2 order `side=ask, price="0.6000"`.
- Trade side: `taker_book_side=ask` means the taker sold YES and consumed YES bids; `taker_book_side=bid` means the taker bought YES and consumed YES asks. This reading comes from one documentation example, so the first recorded day must confirm it by checking which ladder shrinks on the same `clock`.

**Per phantom order (side, p, size S in 0.01-contract units, t_send)**

1. **Arrival.** `t_arrive = t_send + L`, where L is sampled from **our own measured** latency distribution (V2 response `ts_ms` minus local send time). There is no default constant.
2. **Marketable at arrival?**
   - A post-only order is cancelled (`PostOnlyCrossCancel`). Post-only behaviour on the order book is undocumented, so test it on demo.
   - Otherwise it is a taker order. It walks the displayed levels **at t_arrive** and pays the taker fee.
3. **Queue ahead.** `A0` is the full displayed size at (side, p) at t_arrive. We join behind everything. Off-grid prices are rejected.
4. **Event loop, ordered by `clock` then `seq`.**
   - **Trade at p against our side, volume v.** If `v > A`, fill `min(S_rem, v − A)` and set `A = 0`; otherwise `A −= v`. Same-clock prints are summed first.
   - **Trade-through**, meaning a print at a price better for the aggressor than p. Fill `min(S_rem, aggressor volume)`, because price priority put us ahead.
   - **Book cross with no print.** Fill and tag `phantom_cross`. These fills are reported separately as a known source of optimism.
   - **Unexplained decrease at p** (a cancel). `A −= min(A, d·φ)`. **A cancel alone never produces a fill.**
   - **Increase at p.** It joins behind us; `A` is unchanged.
   - **Our own amend.** A size decrease or an expiry-only change keeps `A`. A price change or size increase sends us to the back of the queue.
   - **Pause or close.** Cancel if `cancel_order_on_pause` is set; at close, cancel whatever is resting.
5. **Fill price** is always p, never a better later print. Fees are in §5.
6. **Outputs per phantom:**
   - fill, partial fill or no fill, with time to fill;
   - the trajectory of `A`;
   - the fill cause (trade-at-price, trade-through or phantom_cross);
   - **markouts at +1 s, +10 s, +60 s and settlement.** Fill-conditional markout is what killed every past maker result.
7. **Size realism.** Flag any fill where S exceeds 10% of the counterparty volume, because a replay cannot react to our order.

**φ (cancel-ahead fraction)** is the only free parameter. Until it is calibrated, every result is reported as a **bracket**:
- **floor:** φ = 0 (trades only);
- **ceiling:** φ proportional, A/(D − S_own);
- **calibrated:** φ = φ̂.

Dove's measurements on a real CME tape show why the bracket matters. A proportional-cancel rule over-predicted the fill rate about 4×. The trades-only floor had about the right rate but produced 222,627 false fills. An exact-queue model brought the strategy-matched error to 0.003. That tape was CME, not Kalshi, so these numbers motivate the method; they are not Kalshi measurements.

## 5. Fees, settlement and ledger

Source: `w2-sim-architecture` §3–5, with the fee-schedule PDF effective 2026-07-07.

**Fees per fill**
- Taker: `0.07·C·P(1−P)` × the series taker multiplier.
- Maker: `0.0175·C·P(1−P)` × the series **maker multiplier, which defaults to 0**.
- Read `fee_type` and `fee_multiplier` per series from `/series` and `/series/fee_changes`. Examples:
  - KXMVE combos: maker multiplier 1, taker multiplier 2.
  - All six Emmy series charge makers.
  - KXMLBGAME uses quadratic_with_maker_fees with multiplier 0.5.
- Round up to $0.000001. Direct-member balances align to $0.0001 (since 2026-05-28), and there is a per-order rounding accumulator.
- Compute in Decimal, never float. In float, ceil(0.07·100·0.8·0.2) gives 113c; the correct fee is 112c.

**Settlement**
- Outcomes come only from Kalshi's settlement; the strike comes from market metadata.
- Never use Binance or other spot candles as the outcome.

**Ledger invariants**
- 0 < price < 1 and fee ≥ 0.
- P&L stays within max-loss and max-profit bounds.
- Every live row has an exchange order id.
- Live fills come only from the `fill` stream or `/portfolio/fills`, with a `trade_id`.
- A fill that arrives after a cancel is handled.

**Reconciliation, nightly**
- Ledger positions match `/portfolio/positions`.
- Ledger P&L matches Kalshi settlements to $0.0001.
- Shard balances are checked. Crypto is on shard 2, which has its own collateral and needs `exchange_index` on every cancel.

## 6. Calibration loop: making paper match live

Source: `w2-sim-demo-env` §6. **Demo cannot be used for this.** It is a separate, nearly empty exchange: on the same market in the same minute it showed 54 contracts of volume against 753,243 in production, with different prices. The reference strikes do match. Use demo only to test the V2 port and order logic.

1. **Record continuously** on the target series.
2. **Shadow every decision** through KROS.
3. **Twin probes on production.** Pick a random subsample of shadow decisions, about 1 in 5, chosen at random so that probe selection cannot correlate with the signal. Send each as a real order of 0.1–1 contract: post-only for maker probes, IOC for taker probes.
   - Poll `GET /portfolio/orders/{id}/queue_position` or the batch `queue_positions` endpoint. It is 0-indexed: the first order in the queue returns 0.00. It is not yet known whether the count includes our own size.
   - Each step gives the true change in A. Subtracting the matched trade volume gives a **measured** φ̂, by series, price band, distance from the touch, depth and time to close.
   - Batch REST writes bill only the unscoped rate bucket. Auto-routed single writes bill the unscoped bucket and every nonzero shard's bucket.
4. **Pair and score each twin:** fill or no fill, time to fill, predicted against actual queue trajectory, fill price, and markout at +10 s, +60 s and settlement. Report bias (real minus sim) per cell, with CIs clustered by calendar day.
5. **Freeze and re-score.** Freeze φ̂ and L with a hash (harness `preregistration.py`) and re-score every paper result. Re-calibrate monthly, and after any change to the tick structure or fees.
6. **Kill rule.** If the real fill-conditional markout falls below the sim's by more than the strategy's claimed edge, the strategy is dead before any sizing.

**Budget**
- Fill probability to within ±10 pp needs about 96 twins per cell; ±5 pp needs about 384.
- Markout is expensive: detecting a 5c bias needs about 246 fills, and a 2c bias about 1,537.
- At the past adverse selection of about 3.5c per contract, 384 one-lot fills cost about $13 expected, or about $1.30 at 0.1 lot. Concurrent capital is capped at about $10.
- **The binding constraint is time in thin cells, not money.**

## 7. Acceptance gate: 20 checks (all must pass before any paper number is cited)

Source: `w2-sim-past-errors`. No existing simulator passes this list.

| # | Check | Pass condition |
|---|---|---|
| 1 | Price source | Fills reference the reconstructed book (`seq`/`clock`) at decision + latency. Never the strategy's snapshot, the mid, the last trade, a candle or a model price. |
| 2 | Disappearing quote | A quote that vanishes 50 ms before arrival produces no fill. |
| 3 | Depth | Filled quantity is at most the depth through the limit. Marketable orders walk the levels, and a buy's VWAP is at least the best ask. |
| 4 | Empty or missing quotes | A missing field means skip. An empty side (the 100/0 sentinel) gives no price. |
| 5 | Freshness | `book_age_ms` is logged per fill. An injected gap gives 0 fills and sets the dirty flag. |
| 6 | Look-ahead | Every input carries `available_at` ≤ `decision_ts`. The guard covers all fields, and its test builds a real object. |
| 7 | Latency | `latency_ms` comes from measured p50 and p95, and P&L is reported at both. |
| 8 | Resting fills | FIFO queue ahead, correct aggressor side, partial fills, calibrated against real `queue_position`. |
| 9 | YES/NO symmetry | A mirrored market gives identical P&L. A NO buy against a YES bid of 7c fills at 93c. |
| 10 | Fee golden table | Matches Kalshi's schedule for p = 1..99, every size, maker and taker, and each multiplier. Replaying real fills matches to $0.0001. |
| 11 | Fill verification | Ledger fills come only from `fill` or `/portfolio/fills` with a `trade_id`. An injected fill after cancel is caught. |
| 12 | Nightly reconcile | Positions and P&L match Kalshi to $0.0001, and a diff report is stored. |
| 13 | Outcome source | Labels come only from Kalshi settlement, and strikes from market metadata. |
| 14 | Ledger invariants | Price bounds, fee ≥ 0, P&L bounds and exchange order ids all hold. |
| 15 | Time | Ticker and close_time round-trip in America/New_York across DST. No naive datetimes. An `event_day` column exists for clustering. |
| 16 | Mode separation | Paper and live keep separate risk state. The paper balance starts at the real balance, and sizing uses the same function. |
| 17 | Full-replay counterfactuals | Rule changes are evaluated by re-simulating every candidate, winners included. |
| 18 | Search ledger | Every searched cell is counted. Gates are numerically satisfiable. P(observed OOS \| breakeven) is reported. |
| 19 | No-information floor | Random entry on the same events scores ≤ 0, about −(half-spread + fee). |
| 20 | Paper↔live fidelity | Run 1-lot live twins for at least 2 weeks. On the strategy-matched stratum, the fill-rate error is ≤ 0.05, the false-fill rate ≤ 1%, and the φ bracket is reported. |

Extra gate before any strategy is scaled: the P&L bracket must be **positive at both ends**, φ = 0 and φ proportional.

## 8. Kill switch, four layers

1. **On the exchange.** An order group with a rolling 15-second contract cap, `cancel_order_on_pause`, and short `expiration_time` values extended by expiry-only amends, which keep queue priority.
2. **A separate risk monitor.** It reads the Kalshi account balance and positions, not the strategy's own accounting. It enforces the **$100 hard loss cap** by halting and calling cancel-all.
3. **Host watchdogs and an off-box heartbeat.**
4. **A manual cancel-all.**

## 9. V2 order port checklist

Source: `w2-sim-v2-orders-port` (verifier: solid).

**Endpoint and pricing**
- `POST /portfolio/events/orders` uses a single YES-leg book: `side=bid` buys YES, and `side=ask` sells YES, which is the same as going long NO.
- `price` and `count` are fixed-point strings on the `price_ranges` grid.
- Orders can be as small as 0.01 contracts.

**Fields**
- Required:
  - `time_in_force`: FOK, GTC or IOC.
  - `self_trade_prevention_type`: taker_at_cross or maker.
- Optional:
  - `post_only`.
  - `reduce_only`, valid with IOC only.
  - `expiration_time`.
  - `order_group_id`.
  - `subaccount`.
  - `exchange_index`.

**Amend and responses**
- Amending the price or increasing the size loses queue priority. Decreasing the size or changing only the expiry keeps it.
- The response returns `fill_count`, `average_fill_price` and `average_fee_paid` synchronously.

**Gaps in the old code**
- Shards: shard 2 (crypto) has its own balance and must be funded.
- Status `unknown`: razorbillaws' `is_terminal()` does not handle it. Treat it as non-terminal and reconcile.

**Reuse**
- razorbillaws `kalshi.py`: the RSA-PSS signer.
- razorbillaws `kalshi_ws.py`: the WS client.
- razorbillaws `reconcile.py`.
- Harness `preregistration.py` and `family_budget.py`, which work as shipped.
- **Rewrite** the harness's fill rule and fee model. `power.py` and `program_verdict.py` do not import.

## 10. Build order

1. **Recorder (no money).** Start on day 1, because every day not recorded is lost.
2. **V2 gateway against demo (no money).** Order logic, post-only behaviour, amend priority, shards, and whether the collateral rule sums or nets the two sides of a quote.
3. **KROS replay plus the acceptance tests (no money).** Checks 1–19 run as `pytest` against the recorded tape.
4. **Shadow mode on the live stream (no money).**
5. **Paper-trade every hypothesis** across all eligible markets in shadow mode, with no collateral limit. Use the pre-registered tests (H1/H2 drafts in `notes/w2-h1h2-preregistration.md`) and report the φ bracket.
6. **Optional twin probes (real money, only with Jackson's explicit go-ahead).** Fractional sizes, about $10 concurrent. They produce a measured φ̂, L and check 20, which narrows the bracket.
7. **Live** (capped at $100) only after paper clears its pre-registered gate. **Rule (Jackson): paper first, always.**

**Needed from Jackson:** a new GitHub repo, an always-on box (AWS us-east-2, about $12–40 a month), and a Kalshi API key set as environment secrets on that box. The key is never pasted in chat.
