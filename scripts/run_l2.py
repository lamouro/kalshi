"""Paper-test strategies on the recorder's real L2 stream (KROS queue model, floor + ceiling).

    python3 scripts/run_l2.py --root /data/kalshi/ws --prefix KXTRUMPMENTION,KXMAMDANIMENTION \
        [--start 20261106T00 --end 20261107T23] [--results results/l2_results.json] \
        [--meta results/l2_meta.json] --out results/raw/l2_rows.csv.gz

Each market is replayed on its own (paper mode has no collateral coupling). Every fill gets
markouts against the market's own clean mid at 10 s, 60 s, 300 s and 3600 s (cents, from our side,
net of fee per contract), and settlement P&L when the result is known (lifecycle frame or the
--results file: {ticker: "yes"|"no"}). --meta optionally gives {ticker: {event_start_ms, close_ms}}
for the pre-event strategies. Markouts are the main metric here: with real queue fills they
measure spread capture minus adverse selection on every fill, which needs no settlement and gives
far more observations per day than settlement P&L.
"""
from __future__ import annotations

import argparse
import bisect
import gzip
import json
import os
import sys
from collections import Counter, defaultdict
from decimal import Decimal

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from kalshi_engine.book import Book  # noqa: E402
from kalshi_engine.events import BookDelta, BookSnapshot, Gap, Lifecycle  # noqa: E402
from kalshi_engine.replay import L2Replay  # noqa: E402
from kalshi_engine.strategies.core import (BandMaker, PreEventMaker, RandomEntry,  # noqa: E402
                                           ToxicityMaker, TwoSidedMaker)
from kalshi_engine.strategy import Strategy  # noqa: E402
from kalshi_engine.tape_sim import Quote  # noqa: E402
from kalshi_engine.ws_adapter import files, iter_events  # noqa: E402

HORIZONS = (10_000, 60_000, 300_000, 3_600_000)


class QuoteFeed(Strategy):
    """Turn book events into the Quote events the core strategies consume (only on a change of
    the touch, and never on a dirty book)."""

    def __init__(self, inner, books):
        self.inner, self.books, self.last = inner, books, {}
        self.name = inner.name

    def on_event(self, e, ctx):
        if isinstance(e, (BookSnapshot, BookDelta)):
            b = self.books.get(e.ticker)
            if b is None or b.dirty:
                return []
            q = (b.best_bid(), b.best_ask())
            if self.last.get(e.ticker) == q:
                return []
            self.last[e.ticker] = q
            return self.inner.on_event(Quote(e.ticker, e.ts, q[0], q[1]), ctx)
        return self.inner.on_event(e, ctx)


def variants():
    V = []
    for ms in ("0.02", "0.03", "0.05"):
        for imp in (True, False):
            for skew in (None, "sell_yes", "buy_yes"):
                V.append(("two_sided", lambda ms=ms, imp=imp, skew=skew: TwoSidedMaker(
                    min_spread=ms, improve=imp, pull_ms=900_000, max_inv="3", skew_to=skew)))
    for lo, hi in (("0.85", "0.94"), ("0.95", "0.98")):
        for rule in ("join", "improve"):
            V.append(("fav_maker", lambda lo=lo, hi=hi, rule=rule: BandMaker(lo, hi, side_mode="fav", price_rule=rule, max_per_market="3")))
    for lo, hi in (("0.02", "0.10"), ("0.05", "0.20")):
        V.append(("longshot_fade", lambda lo=lo, hi=hi: BandMaker(lo, hi, side_mode="longshot_fade", max_per_market="3")))
    for side in ("sell_yes", "buy_yes", "both"):
        for pull in (15, 60):
            V.append(("pre_maker", lambda side=side, pull=pull: PreEventMaker(side=side, pull_ms=pull * 60_000, max_inv="3")))
    for ms in ("0.03", "0.05", "0.08"):
        for w, tox in ((30_000, "5"), (60_000, "10"), (300_000, "25")):
            for imp in (False, True):
                V.append(("tox_maker", lambda ms=ms, w=w, tox=tox, imp=imp: ToxicityMaker(
                    min_spread=ms, window_ms=w, tox=tox, improve=imp, max_inv="3")))
    V.append(("random_control", lambda: RandomEntry(every=20)))
    return V


def warm_books(paths, prefixes, stats):
    """Rebuild every market's book from earlier files WITHOUT keeping their events. Kalshi sends a
    snapshot only when a subscription starts, so a window that begins after the recorder connected
    has deltas but no snapshot; the carried book becomes that window's opening snapshot. A market
    whose book is dirty at the end of the warm-up (gap, cut file) stays dirty until a real one."""
    books = {}
    for e in iter_events(paths, tickers=prefixes, stats=stats):
        b = books.setdefault(e.ticker, Book(e.ticker))
        if isinstance(e, BookSnapshot):
            b.apply_snapshot(e)
        elif isinstance(e, BookDelta):
            b.apply_delta(e)
        elif isinstance(e, Gap):
            b.mark_dirty(e.reason)
    return books


def split_by_ticker(paths, prefixes, stats, carried=None):
    per = defaultdict(list)
    first_ts = None
    for e in iter_events(paths, tickers=prefixes, stats=stats):
        if first_ts is None:
            first_ts = e.ts
            for t, b in sorted((carried or {}).items()):     # opening snapshots from the warm-up
                if not b.dirty:
                    per[t].append(BookSnapshot(t, first_ts, dict(b.bids), dict(b.asks)))
        per[e.ticker].append(e)
    return per


def mid_history(evs):
    b, hist, result = Book("x"), [], None
    for e in evs:
        if isinstance(e, BookSnapshot):
            b.apply_snapshot(e)
        elif isinstance(e, BookDelta):
            b.apply_delta(e)
        elif isinstance(e, Gap):
            b.mark_dirty(e.reason)
        elif isinstance(e, Lifecycle) and e.kind == "settle" and e.result:
            result = e.result
        if isinstance(e, (BookSnapshot, BookDelta)) and not b.dirty:
            m = b.mid()
            if m is not None and (not hist or hist[-1][1:] != (m, b.best_bid(), b.best_ask())):
                hist.append((e.ts, m, b.best_bid(), b.best_ask()))
    return hist, result


def taker_fee_c(p):
    return 7 * float(p) * (1 - float(p))      # quadratic taker fee, cents per contract (pre-rounding)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default="/data/kalshi/ws")
    ap.add_argument("--prefix", required=True, help="comma-separated ticker prefixes")
    ap.add_argument("--start", default="")
    ap.add_argument("--end", default="~")
    ap.add_argument("--results", default="")
    ap.add_argument("--meta", default="")
    ap.add_argument("--latency-ms", type=int, default=250)
    ap.add_argument("--warm", action=argparse.BooleanOptionalAction, default=True,
                    help="carry books from all files before --start (default on)")
    ap.add_argument("--out", default="results/raw/l2_rows.csv.gz")
    a = ap.parse_args()
    # --root may list several folders (e.g. /data/kalshi/ws,/data/kalshi/ws_archive); merged in hour order
    paths = sorted((x for r in a.root.split(",") for x in files(r, a.start, a.end)), key=os.path.basename)
    stats = Counter()
    prefixes = tuple(a.prefix.split(","))
    carried = None
    if a.start and a.warm:
        allp = sorted((x for r in a.root.split(",") for x in files(r, "", "~")), key=os.path.basename)
        warm = [x for x in allp if os.path.basename(x)[7:18] < a.start]
        carried = warm_books(warm, prefixes, Counter())
        print("warm-up files", len(warm), "books carried", sum(not b.dirty for b in carried.values()), flush=True)
    per = split_by_ticker(paths, prefixes, stats, carried)
    results = json.load(open(a.results)) if a.results else {}
    meta = json.load(open(a.meta)) if a.meta else {}
    print("files", len(paths), "markets", len(per), "parse", dict(stats), flush=True)
    rows, mrows = [], []
    for t, evs in sorted(per.items()):
        hist, res = mid_history(evs)
        res = results.get(t, res)
        if len(hist) < 2:
            continue
        tss = [h[0] for h in hist]
        end_ts = evs[-1].ts
        for fam, mk in variants():
            for mode in ("floor", "ceiling"):
                s = mk()
                rp = L2Replay(None, phi_mode=mode, latency_ms=a.latency_ms)
                rp.s = QuoteFeed(s, rp.books)
                if t in meta:
                    rp.ctx.meta[t] = meta[t]
                rp.run(evs)
                if rp.fill_rows:
                    pos = sum((1 if r["side"] == "bid" else -1) * float(r["qty"]) for r in rp.fill_rows)
                    cash = sum(-(1 if r["side"] == "bid" else -1) * float(r["qty"]) * float(r["price"]) * 100
                               - float(r["fee"]) * 100 for r in rp.fill_rows)
                    last = hist[-1]
                    exit_px = last[2] if pos > 0 else last[3]
                    mrow = dict(family=fam, variant=s.name, mode=mode, ticker=t, event=t.rsplit("-", 1)[0],
                                n_fills=len(rp.fill_rows), contracts=sum(float(r["qty"]) for r in rp.fill_rows),
                                end_pos=pos, pnl_mid_c=cash + pos * float(last[1]) * 100)
                    mrow["pnl_exit_c"] = (cash + pos * float(exit_px) * 100 - abs(pos) * taker_fee_c(exit_px)
                                          if pos == 0 or exit_px is not None else None)
                    mrow["pnl_settle_c"] = cash + pos * (100.0 if res == "yes" else 0.0) if res in ("yes", "no") else None
                    mrows.append(mrow)
                for r in rp.fill_rows:
                    sign = 1 if r["side"] == "bid" else -1
                    px, fee_c = r["price"], float(r["fee"]) / float(r["qty"]) * 100
                    row = dict(family=fam, variant=s.name, mode=mode, ticker=t, event=t.rsplit("-", 1)[0],
                               series=t.split("-")[0], ts=r["ts"], side=r["side"], price=float(px),
                               qty=float(r["qty"]), fee_c=fee_c, taker=r["taker"], cause=r["cause"])
                    for h in HORIZONS:
                        i = bisect.bisect_right(tss, r["ts"] + h) - 1
                        ok = i >= 0 and hist[i][0] > r["ts"] and end_ts >= r["ts"] + h   # full horizon observed
                        row[f"mo_{h // 1000}s"] = float(sign * (hist[i][1] - px) * 100) - fee_c if ok else None
                        # exit value: cross back at the far touch at t+h and pay the taker fee
                        ex = (hist[i][2] if sign > 0 else hist[i][3]) if ok else None
                        row[f"ex_{h // 1000}s"] = (float(sign * (ex - px) * 100) - fee_c - taker_fee_c(ex)
                                                   if ex is not None else None)
                    row["pnl_settle_c"] = (sign * ((1.0 if res == "yes" else 0.0) - float(px)) * 100 - fee_c) if res in ("yes", "no") else None
                    rows.append(row)
        print(t, "events", len(evs), "rows so far", len(rows), flush=True)
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    df = pd.DataFrame(rows)
    with gzip.open(a.out, "wt") as f:
        df.to_csv(f, index=False)
    M = pd.DataFrame(mrows)
    mout = a.out.replace("l2_rows", "l2_markets") if "l2_rows" in a.out else a.out.replace(".csv", "_markets.csv")
    with gzip.open(mout, "wt") as f:
        M.to_csv(f, index=False)
    print("wrote", a.out, len(df), mout, len(M))
    if len(df):
        summarize(df, M)


def summarize(df, M=None):
    import numpy as np
    out = []
    for (v, mode), g in df.groupby(["variant", "mode"]):
        rec = dict(variant=v, mode=mode, n=len(g), markets=g.ticker.nunique(), events=g.event.nunique())
        for col in ("mo_60s", "mo_300s", "mo_3600s", "ex_60s", "ex_300s", "ex_3600s", "pnl_settle_c"):
            x = g.dropna(subset=[col])
            if len(x) < 2:
                continue
            w = x.groupby("event")[col].agg(["sum", "count"])
            n, k, mu = w["count"].sum(), len(w), w["sum"].sum() / w["count"].sum()
            se = np.sqrt(((w["sum"] - mu * w["count"]) ** 2).sum() * k / (k - 1)) / n if k > 1 else float("nan")
            rec[col], rec[col + "_se"] = round(mu, 2), round(se, 2)
        out.append(rec)
    R = pd.DataFrame(out)
    key = next((c for c in ("mo_300s", "mo_60s", "n") if c in R), "n")
    R = R.sort_values(["mode", key], ascending=[True, False])
    pd.set_option("display.width", 250); pd.set_option("display.max_rows", 500)
    print(R.to_string(index=False))
    R.to_csv("results/l2_summary.csv", index=False)
    if M is not None and len(M):
        # per-market book P&L (cents per market), clustered by event
        out = []
        for (v, mode), g in M.groupby(["variant", "mode"]):
            rec = dict(variant=v, mode=mode, markets=len(g), events=g.event.nunique(), contracts=g.contracts.sum())
            for col in ("pnl_mid_c", "pnl_exit_c", "pnl_settle_c"):
                x = g.dropna(subset=[col])
                if len(x) < 2:
                    continue
                w = x.groupby("event")[col].sum()
                k = len(w)
                rec[col + "_per_mkt"] = round(x[col].mean(), 2)
                rec[col + "_per_ct"] = round(x[col].sum() / x.contracts.sum(), 2)
                rec[col + "_se_per_mkt"] = round(w.std(ddof=1) * np.sqrt(k) / len(x), 2) if k > 1 else float("nan")
            out.append(rec)
        Q = pd.DataFrame(out).sort_values(["mode", "variant"])
        print(Q.to_string(index=False))
        Q.to_csv("results/l2_market_summary.csv", index=False)


if __name__ == "__main__":
    main()
