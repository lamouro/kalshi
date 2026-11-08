"""Settlement results from recorder files: {ticker: 'yes'|'no'} from market_lifecycle_v2 'determined'
frames. Cheap substring prefilter, so a full scan of all data is fast. Feed to run_l2 --results so
every chunk sees settlements that happen after it.

    python3 scripts/collect_results.py --root /data/kalshi/ws,/data/kalshi/ws_archive --out results/l2_results.json
"""
import argparse, glob, gzip, json, os

ap = argparse.ArgumentParser()
ap.add_argument("--root", default="/data/kalshi/ws")
ap.add_argument("--out", default="results/l2_results.json")
a = ap.parse_args()
res = json.load(open(a.out)) if os.path.exists(a.out) else {}
n = 0
for r in a.root.split(","):
    for p in sorted(glob.glob(os.path.join(r, "kalshi_*.jsonl.gz"))):
        try:
            with gzip.open(p, "rt") as fh:
                for line in fh:
                    if "determined" not in line:
                        continue
                    try:
                        m = json.loads(json.loads(line)["raw"])
                    except Exception:
                        continue
                    msg = m.get("msg") or {}
                    if msg.get("event_type") == "determined" and msg.get("result") in ("yes", "no"):
                        res[msg["market_ticker"]] = msg["result"]; n += 1
        except (EOFError, OSError):
            pass
json.dump(res, open(a.out, "w"))
print("determined frames", n, "markets with results", len(res))
