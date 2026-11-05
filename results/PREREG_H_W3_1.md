# Pre-registration H-W3-1: Yes-fade maker on mention strikes (written 2026-11-05, before slice 6 is pulled)

**Found on:** random-strike sample (slice 5, 20 KXTRUMPMENTION events). Pooled cell
`maker_sell_yes_0.05-0.95`, PRE window, held to settlement, floor bracket end:
+5.9c/contract (cluster SE 2.75, 20 events); train +5.1, test +7.3; 14/20 events positive.
Pre-start markout mo_ref ~ 0, so any edge is settlement (Yes overpriced), not spread capture.
The top-volume sample shows the opposite sign; that sample is biased toward Yes (volume spikes when
a word is said), so it is not evidence either way.

**Holdout:** `results/jobs/slice6.json` — 16 events never used before (8 Trump mention, 4 Mamdani,
4 TrumpSay), strikes drawn at random (seed 20261105, volume > 200 filter), PRE window only.

**Test (fixed now, no changes after data arrives):** on holdout rows (`sample == holdout_random`),
pooled sell_yes PreEventMaker fills, band 0.05-0.95, deduplicated as in `mention_report.py`,
floor mode. One-sided cluster-robust t-test by event, H1: mean pnl_c > 0, alpha 0.05. Also report
ceiling. Pass = floor mean > 0 with p < 0.05. Anything else = not confirmed.

**Limits:** tape-touch fills inferred from prints, no queue; paper only.
