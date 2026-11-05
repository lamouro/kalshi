"""Paper-test mention and culture strategies on public trade tapes (tape-touch, bracketed).

Inputs
  /mnt/project-files/kalshi-data/trades/TICKER__{open,pre}.csv   (agents' transcriptions)
  results/mention_jobs.json        strike -> event, result, close_time  (built from the job slices)
  results/event_starts.json        event -> scheduled start ISO (from /milestones), or null

Each (strike, window) is simulated separately and every fill is held to settlement. Clusters are
events; train/test splits by event date (first ~60% of events = train). Output rows feed
analyze_rows.py and combos.evaluate_combos.

Caveats (printed into the ledger notes): the touch is inferred from prints (trade_quotes), windows
are capped at 600 trades (newest kept), and there is no queue: floor fills only on trade-through,
ceiling also on prints at our price.
"""
from __future__ import annotations

import glob
import gzip
import json
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from kalshi_engine.adapters import iso_ms, trade_quotes, trades_csv_to_events  # noqa: E402
from kalshi_engine.experiment import day_of, run_grid  # noqa: E402
from kalshi_engine.strategies.core import (PreEventMaker, PreEventTaker, RandomEntry,  # noqa: E402
                                           TwoSidedMaker)

TR = os.environ.get("KALSHI_TRADES", "/mnt/project-files/kalshi-data/trades")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
H = 3600_000


def load_markets():
    jobs = json.load(open(os.path.join(ROOT, "results/mention_jobs.json")))
    starts = json.load(open(os.path.join(ROOT, "results/event_starts.json")))
    mk = []
    for path in sorted(glob.glob(f"{TR}/*__*.csv")):
        base = os.path.basename(path)[:-4]
        ticker, window = base.rsplit("__", 1)
        j = jobs.get(ticker)
        if j is None or j["result"] not in ("yes", "no"):
            continue
        try:
            ev = trades_csv_to_events(path, ticker, j["close_time"], j["result"])
        except Exception as ex:  # malformed transcription: skip, but say so
            print("skip", base, ex)
            continue
        if len(ev) < 4:
            continue
        ev = trade_quotes(ev)
        st = starts.get(j["event"])
        meta = {"event_start_ms": iso_ms(st) if st else None}
        first = next(e.ts for e in ev)
        mk.append(dict(ticker=ticker, window=window, ev=ev, close_ms=iso_ms(j["close_time"]),
                       day=day_of(first), cluster=j["event"], series=j["event"].split("-")[0],
                       has_start=bool(st), meta=meta, sample=j.get("sample", "top_volume")))
    return mk


def variants_pre():
    V = []
    for side in ("sell_yes", "buy_yes", "both"):
        for pull in (15, 60, 180):
            for rule, off in (("join", 0), ("improve", 0), ("offset", 1)):
                for lo, hi in (("0.05", "0.50"), ("0.50", "0.95"), ("0.05", "0.95")):
                    for ms in ("0.02", "0.05"):
                        V.append(("pre_maker", (lambda side=side, pull=pull, rule=rule, off=off, lo=lo, hi=hi, ms=ms:
                                  PreEventMaker(side=side, pull_ms=pull * 60_000, price_rule=rule, offset_ticks=off,
                                                lo=lo, hi=hi, min_spread=ms))))
    for side in ("sell_yes", "buy_yes"):
        for enter in (3, 24):
            for lo, hi in (("0.05", "0.50"), ("0.50", "0.95")):
                V.append(("pre_taker", (lambda side=side, enter=enter, lo=lo, hi=hi:
                          PreEventTaker(side=side, enter_ms=enter * H, pull_ms=15 * 60_000, lo=lo, hi=hi))))
    # exit-before-go-live variants: quote, then flatten as a taker at the pull time (no event exposure)
    for side in ("sell_yes", "buy_yes", "both"):
        for pull in (15, 60, 180):
            for rule in ("join", "improve"):
                for lo, hi in (("0.05", "0.50"), ("0.05", "0.95")):
                    for inv in ("1", "5"):
                        V.append(("pre_maker_flat", (lambda side=side, pull=pull, rule=rule, lo=lo, hi=hi, inv=inv:
                                  PreEventMaker(side=side, pull_ms=pull * 60_000, price_rule=rule, lo=lo, hi=hi,
                                                min_spread="0.02", max_inv=inv, flatten=True))))
    for ms in ("0.02", "0.03", "0.06"):
        for imp in (True, False):
            for pull in (15, 60):
                V.append(("two_sided_flat", (lambda ms=ms, imp=imp, pull=pull: TwoSidedMaker(
                    min_spread=ms, improve=imp, pull_ms=pull * 60_000, max_inv="5", flatten=True))))
    V.append(("random_control", lambda: RandomEntry(every=4)))
    for ms in ("0.03", "0.06"):
        for imp in (True, False):
            V.append(("two_sided", (lambda ms=ms, imp=imp: TwoSidedMaker(min_spread=ms, improve=imp, pull_ms=15 * 60_000))))
    return V


def main():
    mk = load_markets()
    print("markets(windows)", len(mk), "events", len({m["cluster"] for m in mk}))
    # train/test by event date
    evd = sorted({(m["day"], m["cluster"]) for m in mk})
    order = []
    for d, c in evd:
        if c not in order:
            order.append(c)
    cut = set(order[: int(len(order) * 0.6)])
    rows = []
    V = variants_pre()
    for window in ("pre", "open"):
        sub = [m for m in mk if m["window"] == window]
        if not sub:
            continue
        r = run_grid(sub, V, fees={})
        for x in r:
            x["dataset"] = f"mention_{window}"
            x["split"] = "train" if x["cluster"] in cut else "test"
        rows += r
        print(window, len(sub), "rows", len(r))
    df = pd.DataFrame(rows)
    smap = {m["ticker"]: m["series"] for m in mk}
    if len(df):
        df["series"] = df.ticker.map(smap)
        df["sample"] = df.ticker.map({m["ticker"]: m["sample"] for m in mk})
    os.makedirs(os.path.join(ROOT, "results/raw"), exist_ok=True)
    out = os.path.join(ROOT, "results/raw/mention_rows.csv.gz")
    with gzip.open(out, "wt") as f:
        df.to_csv(f, index=False)
    print("wrote", out, len(df))


if __name__ == "__main__":
    main()
