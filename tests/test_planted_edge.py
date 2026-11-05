"""Planted-edge test: the pipeline must recover a known edge, and a fair market must score ~0."""
import random
from decimal import Decimal as D
from kalshi_engine.events import Trade, Lifecycle
from kalshi_engine.tape_sim import TapeSim, Quote
from kalshi_engine.strategies.core import BandMaker
from kalshi_engine.stats import clustered_mean


def synth(n, true_p, price=D("0.90"), seed=1):
    rng = random.Random(seed)
    mkts = []
    for i in range(n):
        t = f"T{i}"
        ev = [Quote(t, 0, price, price + D("0.01"))]
        ev.append(Trade(t, 1000, price - D("0.01"), D(5), "ask"))      # sell print through our bid
        ev.append(Lifecycle(t, 2000, "settle", "yes" if rng.random() < true_p else "no"))
        mkts.append((t, ev))
    return mkts


def run(true_p):
    rows = []
    for t, ev in synth(4000, true_p):
        sim = TapeSim(BandMaker("0.85", "0.95"), mode="floor").run(ev)
        for r in sim.fill_rows:
            rows.append((t, (float(r["settle"]) - float(r["price"])) * 100, 1.0))
    return clustered_mean(rows)


def test_recovers_planted_5c_edge():
    r = run(0.95)          # price 0.90, true win prob 0.95 -> +5c
    assert abs(r["mean"] - 5.0) < 1.5 and r["n"] == 4000


def test_fair_market_scores_zero():
    r = run(0.90)
    assert abs(r["mean"]) < 1.5
