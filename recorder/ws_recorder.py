"""Message-level Kalshi recorder (PAPER-ENGINE-SPEC §3).

Read loop only: receive -> timestamp -> enqueue. A writer thread appends raw frames verbatim,
gzip JSONL per UTC hour, with recv_mono_ns / recv_wall_ns. Integrity: per-sid seq contiguity;
on a gap, a 'gap' row is written and the markets are resubscribed (fresh snapshot). The engine
treats gap windows as DIRTY.

Usage (on the always-on host, keys in env):
  KALSHI_KEY_ID=... KALSHI_PRIVATE_KEY_PATH=... python -m recorder.ws_recorder \
      --out /data/kalshi --series KXTRUMPMENTION,KXFEDMENTION --trades-all
"""
from __future__ import annotations

import argparse
import asyncio
import gzip
import json
import os
import queue
import threading
import time
import urllib.request

import websockets

from .auth import Signer

WS_URL = "wss://api.elections.kalshi.com/trade-api/ws/v2"
REST = "https://api.elections.kalshi.com/trade-api/v2"


def open_markets(series: list[str]) -> list[str]:
    out = []
    for s in series:
        cursor = ""
        while True:
            url = f"{REST}/markets?series_ticker={s}&status=open&limit=200" + (f"&cursor={cursor}" if cursor else "")
            with urllib.request.urlopen(url, timeout=20) as r:
                d = json.load(r)
            out += [m["ticker"] for m in d.get("markets", [])]
            cursor = d.get("cursor") or ""
            if not cursor:
                break
    return out


class Writer(threading.Thread):
    def __init__(self, root: str):
        super().__init__(daemon=True)
        self.q: queue.Queue = queue.Queue()
        self.root = root
        self.cur_hour, self.fh = None, None
        self.started = int(time.time())
        os.makedirs(root, exist_ok=True)

    def run(self):
        while True:
            item = self.q.get()
            if item is None:
                break
            wall_ns = item["recv_wall_ns"]
            hour = time.strftime("%Y%m%dT%H", time.gmtime(wall_ns / 1e9))
            if hour != self.cur_hour:
                if self.fh:
                    self.fh.close()
                # one file per (hour, process start): a crash never leaves a cut gzip member in
                # the middle of a file that later data is appended to
                self.fh = gzip.open(os.path.join(self.root, f"kalshi_{hour}_{self.started}.jsonl.gz"), "at")
                self.cur_hour = hour
            self.fh.write(json.dumps(item, separators=(",", ":")) + "\n")
            if self.q.empty():
                self.fh.flush()


async def record(args):
    signer = Signer()
    w = Writer(args.out); w.start()
    tickers = open_markets(args.series.split(",")) if args.series else []
    if args.tickers:
        tickers += args.tickers.split(",")
    if args.tickers_file and os.path.exists(args.tickers_file):
        tickers += [x.strip() for x in open(args.tickers_file).read().replace(",", "\n").split() if x.strip()]
    tickers = list(dict.fromkeys(tickers))
    last_seq: dict[int, int] = {}
    conn_id = 0
    while True:
        conn_id += 1
        if args.tickers_file and os.path.exists(args.tickers_file):   # pick up markets added since start
            tickers = list(dict.fromkeys(tickers + [x.strip() for x in open(args.tickers_file).read().replace(",", "\n").split() if x.strip()]))
        try:
            hdr = signer.headers("GET", "/trade-api/ws/v2")
            async with websockets.connect(WS_URL, additional_headers=hdr, max_size=2**24, ping_interval=10) as ws:
                mid = 1
                if tickers:
                    await ws.send(json.dumps({"id": mid, "cmd": "subscribe", "params": {
                        "channels": ["orderbook_delta"], "market_tickers": tickers}})); mid += 1
                await ws.send(json.dumps({"id": mid, "cmd": "subscribe", "params": {
                    "channels": ["trade"] if args.trades_all else ["trade"], **({} if args.trades_all else {"market_tickers": tickers})}})); mid += 1
                await ws.send(json.dumps({"id": mid, "cmd": "subscribe", "params": {"channels": ["market_lifecycle_v2"]}}))
                w.q.put({"type": "_connect", "conn_id": conn_id, "recv_wall_ns": time.time_ns(), "tickers": len(tickers)})
                subscribed = set(tickers)
                next_mid = [mid + 1]

                async def refresh():
                    # markets listed after start: re-read the tickers file (kept fresh by a paced
                    # scanner) and subscribe the new ones on their own subscription (new sid)
                    while args.tickers_file:
                        await asyncio.sleep(args.refresh_s)
                        try:
                            new = [x.strip() for x in open(args.tickers_file).read().replace(",", "\n").split() if x.strip()]
                        except OSError:
                            continue
                        add = [x for x in new if x not in subscribed]
                        for i in range(0, len(add), 200):
                            chunk = add[i:i + 200]
                            await ws.send(json.dumps({"id": next_mid[0], "cmd": "subscribe", "params": {
                                "channels": ["orderbook_delta", "trade"], "market_tickers": chunk}}))
                            next_mid[0] += 1
                            subscribed.update(chunk)
                            tickers.extend(chunk)
                        if add:
                            w.q.put({"type": "_added", "n": len(add), "recv_wall_ns": time.time_ns()})

                rtask = asyncio.create_task(refresh())
                try:
                  async for raw in ws:
                      item = {"conn_id": conn_id, "recv_mono_ns": time.monotonic_ns(), "recv_wall_ns": time.time_ns(), "raw": raw}
                      w.q.put(item)
                      try:
                          m = json.loads(raw)
                      except Exception:
                          continue
                      sid, seq = m.get("sid"), m.get("seq")
                      if sid is not None and seq is not None:
                          if m.get("type") == "orderbook_snapshot":
                              last_seq[sid] = seq
                          elif sid in last_seq and seq != last_seq[sid] + 1:
                              w.q.put({"type": "_gap", "sid": sid, "from": last_seq[sid], "to": seq, "recv_wall_ns": time.time_ns()})
                              raise ConnectionError("seq gap -> resubscribe for fresh snapshots")
                          last_seq[sid] = seq
                finally:
                    rtask.cancel()
        except Exception as e:  # reconnect with backoff; every reconnect is a dirty boundary
            w.q.put({"type": "_disconnect", "conn_id": conn_id, "err": repr(e)[:300], "recv_wall_ns": time.time_ns()})
            last_seq.clear()
            await asyncio.sleep(min(30, 2 ** min(conn_id, 5)))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--series", default="")
    ap.add_argument("--tickers", default="")
    ap.add_argument("--trades-all", action="store_true")
    ap.add_argument("--tickers-file", default="", help="file of tickers (comma or newline), re-read every --refresh-s")
    ap.add_argument("--refresh-s", type=int, default=600)
    asyncio.run(record(ap.parse_args()))


if __name__ == "__main__":
    main()
