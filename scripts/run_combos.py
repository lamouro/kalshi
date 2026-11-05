"""Strategy combinations across every dataset tested so far.

Selection is done on TRAIN only; combinations are scored on TEST only (rule 7: never tune on OOS).
  1. Candidates: per dataset, the top K variants by floor-mode TRAIN mean among variants with at
     least MIN_FILLS train fills whose floor AND ceiling train means are both > 0 (rule 10).
  2. Every combination of 1..MAX_SIZE candidates (any mix of datasets) is a trading book that runs
     all its members at once at one contract per signal. Its TEST P&L per contract and per day,
     clustered by day, is reported for both bracket ends.
  3. Every combination goes into the search ledger; Holm and BY are applied over all of them.
Paper mode has no collateral coupling, so a combination's P&L is the sum of its members'; what a
combination can add is diversification (lower variance per day), not a higher per-contract edge.
"""
import itertools
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from kalshi_engine.combos import summarize  # noqa: E402
from kalshi_engine.stats import append_ledger, benjamini_yekutieli, holm  # noqa: E402

K, MIN_FILLS, MAX_SIZE = 5, 30, 3
SRC = {"crypto": "results/raw/crypto_rows.csv.gz", "mention": "results/raw/mention_rows.csv.gz"}


def load():
    out = []
    for name, p in SRC.items():
        if not os.path.exists(p):
            continue
        d = pd.read_csv(p)
        d["dataset"] = d["dataset"] if "dataset" in d else name
        d["dataset"] = d["dataset"].fillna(name)
        d["key"] = d["dataset"] + ":" + d["variant"]
        d["cl"] = d["dataset"].str.split("_").str[0] + ":" + d["cluster"].astype(str)
        out.append(d)
    return pd.concat(out, ignore_index=True)


def main():
    d = load()
    tr = d[d.split == "train"]
    cands = []
    for ds, g in tr.groupby("dataset"):
        rec = []
        for key, h in g.groupby("key"):
            f, c = h[h["mode"] == "floor"], h[h["mode"] == "ceiling"]
            if len(f) < MIN_FILLS or len(c) < MIN_FILLS:
                continue
            sf, sc = summarize(f, "cl"), summarize(c, "cl")
            if sf["mean_c"] > 0 and sc["mean_c"] > 0:
                rec.append((sf["mean_c"], key))
        rec.sort(reverse=True)
        seen, kept = set(), []
        for _, k in rec:
            sig = tuple(sorted(map(tuple, g[(g.key == k) & (g["mode"] == "floor")][["ticker", "ts", "side", "price"]].values.tolist())))
            if sig in seen:
                continue
            seen.add(sig); kept.append(k)
            if len(kept) == K:
                break
        cands += kept
    print("candidates:", len(cands))
    for c in cands:
        print("  ", c)
    te = d[d.split == "test"]
    rows = []
    for r in range(1, MAX_SIZE + 1):
        for combo in itertools.combinations(cands, r):
            sub = te[te.key.isin(combo)]
            rec = dict(combo=" + ".join(combo), size=r)
            for mode in ("floor", "ceiling"):
                s = summarize(sub[sub["mode"] == mode], "cl")
                daily = sub[sub["mode"] == mode].assign(p=lambda x: x.pnl_c * x.qty).groupby("day").p.sum()
                sharpe = daily.mean() / daily.std() if len(daily) > 1 and daily.std() > 0 else np.nan
                rec.update({f"{mode}_n": s["n"], f"{mode}_k": s["k"], f"{mode}_mean_c": s["mean_c"],
                            f"{mode}_se_c": s["se_c"], f"{mode}_p": s["p"], f"{mode}_daily_sharpe": sharpe})
            rows.append(rec)
    R = pd.DataFrame(rows)
    if R.empty:
        print("no combinations (no candidates passed the train filter)")
        return
    pf = R.floor_p.fillna(1).tolist()
    R["floor_p_holm"] = holm(pf)
    R["floor_by_pass"] = benjamini_yekutieli(pf, q=0.10)
    R["both_positive"] = (R.floor_mean_c > 0) & (R.ceiling_mean_c > 0)
    R = R.sort_values("floor_mean_c", ascending=False)
    R.to_csv("results/combos_test.csv", index=False)
    append_ledger("results/search_ledger.csv", [dict(family="combo", variant=x.combo, mode="floor", dataset="combo_test",
                  split="test", n_fills=x.floor_n, k_clusters=x.floor_k, mean_c=round(x.floor_mean_c, 3) if pd.notna(x.floor_mean_c) else None,
                  se_c=None if pd.isna(x.floor_se_c) else round(x.floor_se_c, 3), p=None if pd.isna(x.floor_p) else round(x.floor_p, 4),
                  notes="train-selected candidates, scored on test") for x in R.itertuples()])
    pd.set_option("display.width", 250); pd.set_option("display.max_colwidth", 120)
    print(R.head(25)[["combo", "floor_n", "floor_mean_c", "floor_se_c", "ceiling_mean_c", "floor_daily_sharpe", "floor_p_holm", "floor_by_pass"]].round(3).to_string(index=False))
    print("combinations:", len(R), " both ends positive on test:", int(R.both_positive.sum()),
          " Holm<0.05:", int((R.floor_p_holm < 0.05).sum()), " BY q<0.10:", int(R.floor_by_pass.sum()))


if __name__ == "__main__":
    main()
