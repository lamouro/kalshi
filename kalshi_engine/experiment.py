"""Run a grid of strategy variants over a set of markets; keep every settled fill.

Output: a list of dict rows (variant, family, mode, ticker, cluster, day, ts, side, price, qty,
fee, pnl_c, tag) that the combination analysis and the search ledger read. Each market is
simulated independently (paper mode has no collateral coupling between markets).
"""
from __future__ import annotations

from datetime import datetime, timezone

from .tape_sim import TapeSim


def run_grid(markets, variants, fees, modes=("floor", "ceiling"), latency_ms=250, extra_meta=None):
    rows = []
    for fam, mk in variants:
        for mode in modes:
            for m in markets:
                s = mk()
                sim = TapeSim(s, mode=mode, fees=fees, latency_ms=latency_ms)
                meta = {"close_ms": m["close_ms"]}
                meta.update(m.get("meta", {}))
                sim.ctx.meta[m["ticker"]] = meta
                sim.run(m["ev"])
                for r in sim.fill_rows:
                    if "settle" not in r:
                        continue
                    sign = 1 if r["side"] == "bid" else -1
                    pnl = sign * (float(r["settle"]) - float(r["price"])) * 100 - float(r["fee"]) / float(r["qty"]) * 100
                    rows.append(dict(family=fam, variant=s.name, mode=mode, ticker=m["ticker"],
                                     cluster=m.get("cluster", m["ticker"]), day=m["day"], ts=r["ts"],
                                     side=r["side"], price=float(r["price"]), qty=float(r["qty"]),
                                     fee=float(r["fee"]), taker=r["taker"], pnl_c=pnl, tag=r["tag"]))
    return rows


def day_of(ms):
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
