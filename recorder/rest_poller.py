"""Keyless fallback recorder: polls PUBLIC REST order books and the trade tape.

No API key needed. Lower fidelity than the WS recorder (snapshots every N seconds, so queue
dynamics between polls are unseen) but enough for touch/spread history and a full trade tape.
  python -m recorder.rest_poller --out /data/kalshi_rest --series KXTRUMPMENTION --interval 5
"""
from __future__ import annotations

import argparse
import gzip
import json
import os
import time
import urllib.request

from .ws_recorder import REST, open_markets


def get(url):
    with urllib.request.urlopen(url, timeout=20) as r:
        return json.load(r)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--series", required=True)
    ap.add_argument("--interval", type=float, default=5.0)
    ap.add_argument("--refresh-markets", type=int, default=600)
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    tickers, t_ref, cursors = [], 0, {}
    while True:
        now = time.time()
        if now - t_ref > a.refresh_markets:
            tickers, t_ref = open_markets(a.series.split(",")), now
        hour = time.strftime("%Y%m%dT%H", time.gmtime())
        with gzip.open(os.path.join(a.out, f"rest_{hour}.jsonl.gz"), "at") as fh:
            for t in tickers:
                try:
                    ob = get(f"{REST}/markets/{t}/orderbook")
                    fh.write(json.dumps({"k": "ob", "t": t, "recv_wall_ns": time.time_ns(), "d": ob}) + "\n")
                    tr = get(f"{REST}/markets/trades?ticker={t}&limit=100")
                    fh.write(json.dumps({"k": "tr", "t": t, "recv_wall_ns": time.time_ns(), "d": tr}) + "\n")
                except Exception as e:
                    fh.write(json.dumps({"k": "err", "t": t, "recv_wall_ns": time.time_ns(), "e": repr(e)[:200]}) + "\n")
        time.sleep(max(0.0, a.interval - (time.time() - now)))


if __name__ == "__main__":
    main()
