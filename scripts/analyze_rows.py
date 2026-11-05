"""Summarise per-fill rows: each variant x mode x split, clustered by `cluster` (and by day),
Holm across the family on TRAIN, bracket rule (both floor and ceiling > 0)."""
import sys
import pandas as pd
sys.path.insert(0, ".")
from kalshi_engine.combos import summarize
from kalshi_engine.stats import holm, append_ledger

src, dataset, out = sys.argv[1], sys.argv[2], sys.argv[3]
df = pd.read_csv(src)
recs = []
for (fam, var, mode, split), g in df.groupby(["family", "variant", "mode", "split"]):
    s = summarize(g, "cluster"); sd = summarize(g, "day")
    recs.append(dict(family=fam, variant=var, mode=mode, split=split, n=s["n"], k=s["k"], mean_c=s["mean_c"],
                     se_c=s["se_c"], p=s["p"], k_day=sd["k"], se_day=sd["se_c"], p_day=sd["p"]))
R = pd.DataFrame(recs)
tr = R[R.split == "train"].copy()
tr["p_holm"] = holm(tr.p.fillna(1).tolist())
R = R.merge(tr[["variant", "mode", "p_holm"]], on=["variant", "mode"], how="left")
piv = R.pivot_table(index=["family", "variant"], columns=["mode", "split"], values="mean_c")
piv.columns = [f"{a}_{b}" for a, b in piv.columns]
ses = R[R.split == "train"].pivot_table(index="variant", columns="mode", values="se_c")
ph = R[R.split == "train"].pivot_table(index="variant", columns="mode", values="p_holm")
piv = piv.reset_index().merge(ses.add_prefix("se_train_").reset_index(), on="variant").merge(ph.add_prefix("pholm_").reset_index(), on="variant")
piv["bracket_pos_train"] = (piv.floor_train > 0) & (piv.ceiling_train > 0)
piv["bracket_pos_test"] = (piv.get("floor_test", 0) > 0) & (piv.get("ceiling_test", 0) > 0)
piv.sort_values("floor_train", ascending=False).to_csv(out, index=False)
append_ledger("results/search_ledger.csv", [dict(family=r.family, variant=r.variant, mode=r.mode, dataset=dataset, split=r.split,
              n_fills=r.n, k_clusters=r.k, mean_c=round(r.mean_c, 3), se_c=None if pd.isna(r.se_c) else round(r.se_c, 3),
              p=None if pd.isna(r.p) else round(r.p, 4), notes="bracket") for r in R.itertuples()])
pd.set_option("display.width", 250); pd.set_option("display.max_rows", 200)
cols = ["variant", "floor_train", "ceiling_train", "se_train_floor", "pholm_floor", "floor_test", "ceiling_test"]
print(piv.sort_values("floor_train", ascending=False)[cols].round(2).to_string(index=False))
print("variants:", len(piv), " bracket-positive on train:", int(piv.bracket_pos_train.sum()),
      " ...and on test:", int((piv.bracket_pos_train & piv.bracket_pos_test).sum()),
      " Holm<0.05 (floor):", int((piv.pholm_floor < 0.05).sum()))
