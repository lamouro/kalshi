"""Combine chunked run_l2 outputs (rows + markets files) and print the same summaries.
    python3 scripts/combine_l2.py results/raw/l2_1_*_l2_rows.csv.gz
Per-market rows from different chunks are summed per (variant, mode, ticker) first, since a market
can trade in several chunks (end position carries; P&L at settlement is additive across chunks).
"""
import glob, sys, os
import pandas as pd
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from scripts.run_l2 import summarize  # noqa: E402

paths = sorted(p for a in sys.argv[1:] for p in glob.glob(a))


def rd(p):
    try:
        return pd.read_csv(p)
    except (pd.errors.EmptyDataError, FileNotFoundError):
        return pd.DataFrame()


R = pd.concat([rd(p) for p in paths], ignore_index=True)
mp = [p.replace("l2_rows", "l2_markets") for p in paths]
M = pd.concat([rd(p) for p in mp], ignore_index=True)
if len(M):
    num = ["n_fills", "contracts", "end_pos", "pnl_mid_c", "pnl_exit_c", "pnl_settle_c"]
    M = M.groupby(["family", "variant", "mode", "ticker", "event"], as_index=False)[[c for c in num if c in M]].sum(min_count=1)
print("rows", len(R), "market rows", len(M), "chunks", len(paths))
summarize(R, M)
