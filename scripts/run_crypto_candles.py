"""Paper-test strategy variants on razorbillaws' April 2026 crypto 15-min minute candles (2,000 mkts).
Proxy data (no trades tape, no depth) -> bracket. Split: train = 04-19..21, test = 04-22."""
import json, sys
from decimal import Decimal
import pandas as pd
sys.path.insert(0, ".")
from kalshi_engine.adapters import candles_to_events, iso_ms
from kalshi_engine.fees import SeriesFees
from kalshi_engine.experiment import run_grid, day_of
from kalshi_engine.strategies.core import BandMaker, TwoSidedMaker, RandomEntry

markets = []
for l in open("/home/claude/razorbillaws/kalshi_candles.jsonl"):
    r = json.loads(l)
    if not r["candles"]:
        continue
    ev, res = candles_to_events(r)
    if res is None:
        continue
    cm = iso_ms(r["close_time"])
    markets.append(dict(ticker=r["ticker"], ev=ev, close_ms=cm, day=day_of(cm), cluster=cm))

V = []
for lo, hi in [("0.80", "0.84"), ("0.85", "0.89"), ("0.90", "0.94"), ("0.95", "0.98"), ("0.97", "0.99")]:
    for win in [None, 600_000, 300_000, 120_000]:
        for rule in ["join", "improve"]:
            V.append(("fav_maker", lambda lo=lo, hi=hi, win=win, rule=rule: BandMaker(lo, hi, price_rule=rule, window_ms=win)))
        V.append(("fav_taker", lambda lo=lo, hi=hi, win=win: BandMaker(lo, hi, window_ms=win, taker=True)))
for lo, hi in [("0.01", "0.03"), ("0.01", "0.05"), ("0.05", "0.10"), ("0.10", "0.20")]:
    for win in [None, 300_000]:
        V.append(("longshot_fade_maker", lambda lo=lo, hi=hi, win=win: BandMaker(lo, hi, side_mode="longshot_fade", window_ms=win)))
        V.append(("longshot_fade_taker", lambda lo=lo, hi=hi, win=win: BandMaker(lo, hi, side_mode="longshot_fade", taker=True, window_ms=win)))
for ms in ["0.02", "0.03", "0.05", "0.08"]:
    for pull in [60_000, 180_000, 420_000]:
        for imp in [True, False]:
            V.append(("two_sided_mm", lambda ms=ms, pull=pull, imp=imp: TwoSidedMaker(min_spread=ms, pull_ms=pull, improve=imp)))
V.append(("random_control", lambda: RandomEntry()))
print("variants", len(V), "markets", len(markets), file=sys.stderr)

fees = {s: SeriesFees(Decimal(1), Decimal(0)) for s in ["KXBTC15M","KXETH15M","KXSOL15M","KXXRP15M","KXDOGE15M","KXBNB15M","KXHYPE15M"]}
rows = run_grid(markets, V, fees)
df = pd.DataFrame(rows)
df["split"] = (df.day == "2026-04-22").map({True: "test", False: "train"})
df.to_csv("results/raw/crypto_rows.csv.gz", index=False)
print("rows", len(df), file=sys.stderr)
