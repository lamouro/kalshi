"""Paper-test strategy variants on razorbillaws' April 2026 crypto 15-min minute candles.

Proxy data (no trades tape, no depth): fills are a bracket (floor = trade-through, ceiling =
touch). Split by calendar day: train = 2026-04-19..21, test = 2026-04-22 (burned once looked at).
"""
import json, sys, copy, itertools
from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal

sys.path.insert(0, ".")
from kalshi_engine.adapters import candles_to_events, iso_ms
from kalshi_engine.fees import SeriesFees
from kalshi_engine.tape_sim import TapeSim
from kalshi_engine.stats import clustered_mean, append_ledger
from kalshi_engine.strategies.core import BandMaker, TwoSidedMaker, RandomEntry, Combo

SRC = "/home/claude/razorbillaws/kalshi_candles.jsonl"
markets = []
for l in open(SRC):
    r = json.loads(l)
    if not r["candles"]:
        continue
    ev, res = candles_to_events(r)
    if res is None:
        continue
    cm = iso_ms(r["close_time"])
    day = datetime.fromtimestamp(cm / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
    markets.append(dict(ticker=r["ticker"], ev=ev, res=res, close_ms=cm, day=day))
print("markets with inferred result:", len(markets), file=sys.stderr)

def variants():
    V = []
    for lo, hi in [("0.85", "0.89"), ("0.90", "0.94"), ("0.95", "0.98")]:
        for win in [None, 300_000, 120_000]:
            for rule in ["join", "improve"]:
                V.append(("fav_maker", lambda lo=lo, hi=hi, win=win, rule=rule: BandMaker(lo, hi, price_rule=rule, window_ms=win)))
            V.append(("fav_taker", lambda lo=lo, hi=hi, win=win: BandMaker(lo, hi, window_ms=win, taker=True)))
    for lo, hi in [("0.01", "0.05"), ("0.05", "0.10"), ("0.10", "0.20")]:
        V.append(("longshot_fade_maker", lambda lo=lo, hi=hi: BandMaker(lo, hi, side_mode="longshot_fade")))
        V.append(("longshot_fade_taker", lambda lo=lo, hi=hi: BandMaker(lo, hi, side_mode="longshot_fade", taker=True)))
    for ms in ["0.02", "0.03", "0.05"]:
        for pull in [60_000, 180_000]:
            V.append(("two_sided_mm", lambda ms=ms, pull=pull: TwoSidedMaker(min_spread=ms, pull_ms=pull, improve=True)))
    V.append(("random_control", lambda: RandomEntry()))
    return V

fees = {s: SeriesFees(Decimal(1), Decimal(0)) for s in ["KXBTC15M","KXETH15M","KXSOL15M","KXXRP15M","KXDOGE15M","KXBNB15M","KXHYPE15M"]}
rows_out = []
results = {}
for fam, mk in variants():
    for mode in ["floor", "ceiling"]:
        per = defaultdict(list)
        name = None
        nfills = 0
        for m in markets:
            s = mk(); name = s.name
            sim = TapeSim(s, mode=mode, fees=fees)
            sim.ctx.meta[m["ticker"]] = {"close_ms": m["close_ms"]}
            sim.run(m["ev"])
            for r in sim.fill_rows:
                if "settle" not in r:
                    continue
                sign = 1 if r["side"] == "bid" else -1
                pnl = sign * (float(r["settle"]) - float(r["price"])) * 100 - float(r["fee"]) / float(r["qty"]) * 100
                split = "test" if m["day"] == "2026-04-22" else "train"
                per[split].append((m["close_ms"], pnl, float(r["qty"])))
                nfills += 1
        for split in ["train", "test"]:
            cm = clustered_mean(per[split])
            results[(name, mode, split)] = cm
            rows_out.append(dict(family=fam, variant=name, mode=mode, dataset="razorbillaws_candles_2026-04", split=split,
                                 n_fills=cm["n"], k_clusters=cm["k"], mean_c=None if cm["mean"] is None else round(cm["mean"], 3),
                                 se_c=None if cm["se"] is None else round(cm["se"], 3), p=None if cm["p"] is None else round(cm["p"], 4),
                                 notes="candle proxy; bracket"))
        tr = results[(name, mode, "train")]
        print(f"{fam:20s} {name:45s} {mode:7s} train n={tr['n']:5d} mean={tr['mean'] if tr['mean'] is None else round(tr['mean'],2)} se={tr['se'] if tr['se'] is None else round(tr['se'],2)}", flush=True)
append_ledger("results/search_ledger.csv", rows_out)
json.dump({"|".join(k): v for k, v in results.items()}, open("results/crypto_candles.json", "w"), indent=1, default=str)
