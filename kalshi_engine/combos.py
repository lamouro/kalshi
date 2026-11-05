"""Combination analysis over per-fill rows from experiment.run_grid.

A combination is a set of variants traded together (paper: no collateral coupling). Its
per-cluster P&L is the sum of its members' P&L in that cluster; we report cents per contract
and cents per cluster-day with cluster-robust SEs. Weights (if any) are fitted on TRAIN only.
Every combination evaluated is a variant for the search ledger.
"""
from __future__ import annotations

import itertools
from collections import defaultdict

import numpy as np
import pandas as pd
from scipy import stats as st


def per_cluster(df: pd.DataFrame, key="cluster"):
    g = df.groupby(key).agg(pnl=("pnl_w", "sum"), qty=("qty", "sum"))
    return g


def summarize(df: pd.DataFrame, key="cluster"):
    if df.empty:
        return dict(n=0, k=0, mean_c=np.nan, se_c=np.nan, p=np.nan, total=0.0)
    df = df.assign(pnl_w=df.pnl_c * df.qty)
    g = per_cluster(df, key)
    W = g.qty.sum()
    mean = g.pnl.sum() / W
    k = len(g)
    if k < 2:
        return dict(n=len(df), k=k, mean_c=mean, se_c=np.nan, p=np.nan, total=g.pnl.sum())
    resid = g.pnl - mean * g.qty
    se = np.sqrt(k / (k - 1) * (resid ** 2).sum() / W ** 2)
    t = mean / se if se > 0 else 0
    return dict(n=len(df), k=k, mean_c=mean, se_c=se, p=2 * st.t.sf(abs(t), k - 1), total=g.pnl.sum())


def evaluate_combos(rows, members, mode, split_col="split", max_size=3, key="cluster"):
    df = pd.DataFrame(rows)
    df = df[df["mode"] == mode]
    out = []
    for r in range(1, max_size + 1):
        for combo in itertools.combinations(members, r):
            sub = df[df.variant.isin(combo)]
            for split in sorted(sub[split_col].unique()):
                s = summarize(sub[sub[split_col] == split], key)
                out.append(dict(combo="+".join(combo), size=r, mode=mode, split=split, **s))
    return pd.DataFrame(out)
