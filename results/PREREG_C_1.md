# Pre-registration C-1: makers on BTC 15-minute markets, real L2 (written 2026-11-06 ~18:45 UTC, before any KXBTC15M data is recorded)

**Why:** exploratory crypto run (archive, 2 settled events) cannot judge anything. KXBTC15M settles
~96 times a day, so one week gives hundreds of independent settled events.

**Data:** recorder files from the first full UTC hour after KXBTC15M is added (restart time logged in
PLAN.md) for 7 days. Prefix `KXBTC15M` only. `scripts/run_l2.py` with default variant list, `--warm`,
latency 250 ms, run in day chunks; settlement joined across chunks from lifecycle `determined` frames.

**Families and primary metric:** settlement P&L per fill (cents, net of fees: KXBTC15M fee schedule
as in kalshi_engine/fees.py; if maker fee applies it is charged), clustered by event (one 15-minute
window = one event). Variants tested: everything run_l2.variants() returns at commit 6daf332.
One-sided t, H1 mean > 0, floor mode; Holm across all variants.
Pass = Holm p < 0.05 AND ceiling mode > 0 AND random_control < 0 AND the variant's per-day means are
positive on at least 5 of 7 days.
**Secondary (descriptive):** ex_300s, per-market book P&L at exit and settlement, by price band.

**No changes** after the window opens; new ideas get a new pre-registration on later days. Paper only.
