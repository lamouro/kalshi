# Pre-registration L2-1: makers on real recorded order books (written 2026-11-06 ~02:30 UTC)

**Why:** first L2 runs (2026-11-05 22:00 to 2026-11-06 01:59 UTC, already seen) show maker fills
gain +1.3 to +3.3c vs the mid at 5 min, but -1.7 to -3.5c when exited immediately by crossing
(spread + taker fee > edge). No market had settled. Untested: passive exits and holding to
settlement. New family `tox_maker` (ToxicityMaker, commit after ce09b24) targets adverse selection
with a taker-flow filter and only passive inventory unwinds; designed before the test data exists.

**Test data:** recorder hours `20261106T03` through `20261109T23` (UTC) only. Nothing in that window
has been looked at. Run `scripts/run_l2.py` with `--start 20261106T03 --end 20261109T23` over all
recorded mention/culture prefixes, unchanged variant list.

**Primary metric:** per-market book P&L at settlement (`pnl_settle_c` in l2_markets), cents per
market, clustered by event, for markets whose settlement is in the data or fetched from
/historical/markets. One-sided t (H1 > 0) per variant, floor mode; Holm over every variant in the
list (all families). Pass = Holm p < 0.05 AND ceiling mode also > 0 AND random_control < 0.
**Secondary (descriptive only):** pnl_exit_c, ex_300s, per-family pooled means.

**No changes** to variants, prefixes or metric after the test window opens; any new idea gets its
own pre-registration on later data. Paper only.

## Amendment 1 (2026-11-06 ~16:00 UTC, before any test-window result was seen)
Method fix, not a change of hypothesis: Kalshi sends book snapshots only when a subscription
starts, so a window starting after the recorder connected has no snapshot and every book stays
dirty (zero fills). run_l2.py now carries each market's book from all earlier files (`--warm`,
default on) as that window's opening snapshot. The L2-1 run uses this default. Variants, prefixes,
window and metric are unchanged.
