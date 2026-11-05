"""Scoring: net cents per contract, clustered CIs, search ledger, Holm / BY corrections."""
from __future__ import annotations

import csv
import math
import os
from collections import defaultdict

import numpy as np
from scipy import stats as st


def per_contract_pnl(ledger, fill_rows):
    """Settlement P&L per fill, cents per contract, net of fees: list of (cluster, pnl_c, qty)."""
    out = []
    for r in fill_rows:
        if "settle" not in r:
            continue
        sign = 1 if r["side"] == "bid" else -1
        gross = sign * (float(r["settle"]) - float(r["price"])) * 100
        fee_c = float(r["fee"]) / float(r["qty"]) * 100
        out.append((r.get("cluster", r["ticker"]), gross - fee_c, float(r["qty"]), r["tag"]))
    return out


def clustered_mean(rows):
    """rows: (cluster, value, weight). Cluster-robust mean, SE, t, p (two-sided), n, k."""
    if not rows:
        return dict(mean=None, se=None, t=None, p=None, n=0, k=0)
    by = defaultdict(lambda: [0.0, 0.0])
    for c, v, w in rows:
        by[c][0] += v * w
        by[c][1] += w
    W = sum(b[1] for b in by.values())
    mean = sum(b[0] for b in by.values()) / W
    k = len(by)
    if k < 2:
        return dict(mean=mean, se=None, t=None, p=None, n=len(rows), k=k)
    resid = [(b[0] - mean * b[1]) for b in by.values()]
    var = k / (k - 1) * sum(r * r for r in resid) / (W * W)
    se = math.sqrt(var)
    t = mean / se if se > 0 else 0.0
    p = 2 * st.t.sf(abs(t), df=k - 1)
    return dict(mean=mean, se=se, t=t, p=p, n=len(rows), k=k)


def holm(pvals):
    m = len(pvals)
    order = sorted(range(m), key=lambda i: pvals[i])
    adj = [None] * m
    run = 0.0
    for rank, i in enumerate(order):
        run = max(run, min(1.0, (m - rank) * pvals[i]))
        adj[i] = run
    return adj


def benjamini_yekutieli(pvals, q=0.10):
    m = len(pvals)
    if m == 0:
        return []
    c = sum(1.0 / i for i in range(1, m + 1))
    order = sorted(range(m), key=lambda i: pvals[i])
    passed = [False] * m
    kmax = -1
    for rank, i in enumerate(order, 1):
        if pvals[i] <= rank * q / (m * c):
            kmax = rank
    for rank, i in enumerate(order, 1):
        passed[i] = rank <= kmax
    return passed


LEDGER_FIELDS = ["family", "variant", "mode", "dataset", "split", "n_fills", "k_clusters",
                 "mean_c", "se_c", "p", "fill_rate", "notes"]


def append_ledger(path, rows):
    new = not os.path.exists(path)
    with open(path, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=LEDGER_FIELDS)
        if new:
            w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k) for k in LEDGER_FIELDS})
