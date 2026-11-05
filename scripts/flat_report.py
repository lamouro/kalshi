"""Exit-before-go-live variants: P&L per market (cents, round trips netted), cluster-robust by event."""
import sys, re
import numpy as np, pandas as pd
d = pd.read_csv("results/raw/mention_rows.csv.gz")
d = d[d.family.isin(["pre_maker_flat", "two_sided_flat", "two_sided", "pre_maker"]) & (d.dataset == "mention_pre")]
d["p"] = d.pnl_c * d.qty
m = d.groupby(["family", "variant", "mode", "split", "cluster", "ticker"]).agg(p=("p", "sum"), q=("qty", "sum"), n=("p", "size")).reset_index()
def cl(g):
    e = g.groupby("cluster").p.agg(["sum", "count"]); k = len(e); n = e["count"].sum()
    mu = e["sum"].sum() / n
    se = np.sqrt(((e["sum"] - mu * e["count"]) ** 2).sum() * k / max(k - 1, 1)) / n if k > 1 else np.nan
    return pd.Series(dict(markets=n, events=k, c_per_market=mu, se=se, contracts=g.q.sum()))
R = m.groupby(["family", "variant", "mode", "split"]).apply(cl).reset_index()
P = R.pivot_table(index=["family", "variant"], columns=["mode", "split"], values="c_per_market")
P.columns = [f"{a}_{b}" for a, b in P.columns]
S = R[(R["mode"] == "floor") & (R.split == "train")].set_index("variant")[["se", "markets", "events"]]
P = P.reset_index().merge(S, left_on="variant", right_index=True, how="left")
P["bracket_train"] = (P.floor_train > 0) & (P.ceiling_train > 0)
P["bracket_test"] = (P.floor_test > 0) & (P.ceiling_test > 0)
P = P.sort_values("floor_train", ascending=False)
P.to_csv("results/flat_summary.csv", index=False)
pd.set_option("display.width", 250); pd.set_option("display.max_rows", 400)
for fam, g in P.groupby("family"):
    print(f"== {fam}: {len(g)} variants, train bracket-positive {int(g.bracket_train.sum())}, and test {int((g.bracket_train & g.bracket_test).sum())}; family mean floor train {g.floor_train.mean():.2f} test {g.floor_test.mean():.2f}")
    print(g.head(8)[["variant", "floor_train", "ceiling_train", "se", "markets", "events", "floor_test", "ceiling_test"]].round(2).to_string(index=False))
