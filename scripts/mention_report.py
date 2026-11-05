"""Coarse, lower-variance read of the mention grid: pool variants by side x band x window x mode,
and report settlement P&L and pre-start markouts (mo_ref: to the last print before event start),
cluster-robust by event. One row per pooled cell; each market contributes its first fill per
variant-cell only once (dedup ticker x cell) to avoid counting duplicate variants as evidence."""
import re, sys
import numpy as np, pandas as pd
sys.path.insert(0, ".")
import os
d = pd.read_csv("results/raw/mention_rows.csv.gz")
SAMPLE = os.environ.get("SAMPLE")  # random | top_volume | unset = all
if SAMPLE:
    d = d[d["sample"] == SAMPLE]
def cell(v):
    m = re.match(r"pre_(sell_yes|buy_yes|both)_\d+m_(\w+?)\d?_([\d.]+-[\d.]+)_", v)
    if m: return f"maker_{m.group(1)}_{m.group(3)}"
    m = re.match(r"pretaker_(sell_yes|buy_yes)_(\d+)m_([\d.]+-[\d.]+)", v)
    if m: return f"taker_{m.group(1)}_{m.group(3)}"
    if v.startswith("mm_"): return "two_sided"
    return v
d["cell"] = d.variant.map(cell)
# a 'both' maker fill is a buy or a sell; label by actual side
d.loc[d.cell.str.startswith("maker_both"), "cell"] = d.cell.str.replace("both", "both") + "_" + d.side
def cl(g, col):
    g = g.dropna(subset=[col])
    if not len(g): return (np.nan, np.nan, 0, 0)
    w = g.groupby("cluster")[col].agg(["sum", "count"])
    n, k = w["count"].sum(), len(w)
    mu = w["sum"].sum() / n
    if k < 2: return (mu, np.nan, n, k)
    r = (w["sum"] - mu * w["count"]) 
    se = np.sqrt((r ** 2).sum() * k / (k - 1)) / n
    return (mu, se, n, k)
# dedup: one fill per (ticker, cell, mode, dataset, side, price, ts)
u = d.drop_duplicates(["ticker", "cell", "mode", "dataset", "side", "ts", "price"])
rows = []
for (ds, c, mode), g in u.groupby(["dataset", "cell", "mode"]):
    p = cl(g, "pnl_c"); r = cl(g, "mo_ref"); h = cl(g, "mo_3600s")
    rows.append(dict(dataset=ds, cell=c, mode=mode, n=p[2], k=p[3], pnl=p[0], pnl_se=p[1],
                     mo_ref=r[0], mo_ref_se=r[1], n_ref=r[2], mo_1h=h[0], mo_1h_se=h[1]))
R = pd.DataFrame(rows)
pd.set_option("display.width", 220); pd.set_option("display.max_rows", 300)
print(R.round(2).to_string(index=False))
R.to_csv(f"results/mention_cells{'_' + SAMPLE if SAMPLE else ''}.csv", index=False)
