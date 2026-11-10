"""L2-1 verdict, exactly as results/PREREG_L2_1.md (+ amendments) specifies.

Primary metric: per-market book P&L at settlement (pnl_settle_c, cents per market), clustered by
event; one-sided t (H1 mean > 0) per variant in floor mode; Holm over every variant.
Pass = Holm p < 0.05 AND the same variant's ceiling-mode mean > 0 AND random_control mean < 0.

    python3 scripts/verdict_l2_1.py "results/raw/l2_1_*_l2_markets.csv.gz"
"""
import glob, sys, os
import numpy as np, pandas as pd
from scipy import stats
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from kalshi_engine.stats import holm  # noqa: E402


def rd(p):
    try:
        return pd.read_csv(p)
    except pd.errors.EmptyDataError:
        return pd.DataFrame()


M = pd.concat([rd(p) for a in sys.argv[1:] for p in sorted(glob.glob(a))], ignore_index=True)
M = M.dropna(subset=["pnl_settle_c"])
M = M.groupby(["family", "variant", "mode", "ticker", "event"], as_index=False)["pnl_settle_c"].sum()


def test(g):
    w = g.groupby("event").pnl_settle_c.sum()            # event cluster totals
    n, k = len(g), len(w)
    mu = g.pnl_settle_c.mean()
    if k < 2:
        return mu, np.nan, n, k, 1.0
    se = np.sqrt(((w - mu * g.groupby("event").size()) ** 2).sum() * k / (k - 1)) / n
    t = mu / se if se > 0 else 0.0
    return mu, se, n, k, float(1 - stats.t.cdf(t, k - 1))


rows = []
for (v, mode), g in M.groupby(["variant", "mode"]):
    mu, se, n, k, p = test(g)
    rows.append(dict(variant=v, family=g.family.iloc[0], mode=mode, markets=n, events=k, mean_c=mu, se_c=se, p_one=p))
R = pd.DataFrame(rows)
F = R[R["mode"] == "floor"].copy()
F["p_holm"] = holm(F.p_one.fillna(1).tolist())
C = R[R["mode"] == "ceiling"].set_index("variant").mean_c
rc = F[F.family == "random_control"].mean_c
rc_ok = bool(len(rc) and (rc < 0).all())
F["ceiling_mean_c"] = F.variant.map(C)
F["PASS"] = (F.p_holm < 0.05) & (F.ceiling_mean_c > 0) & rc_ok
pd.set_option("display.width", 220); pd.set_option("display.max_rows", 200)
print(F.sort_values("p_one").round(4).to_string(index=False))
print("random_control floor mean < 0:", rc_ok, "| variants:", len(F), "| PASS:", int(F.PASS.sum()))
F.to_csv("results/l2_1_verdict.csv", index=False)
