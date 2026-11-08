#!/usr/bin/env bash
# Run run_l2.py over a window in CHUNK-hour pieces, carrying book state between chunks and using
# one settlement file for all chunks.  Usage:
#   scripts/run_l2_chunks.sh START END PREFIXES TAG [CHUNK_HOURS] [ROOTS]
#   e.g. scripts/run_l2_chunks.sh 20261106T03 20261109T23 KXTRUMPMENTION,... l2_1 6
set -euo pipefail
START=$1; END=$2; PREF=$3; TAG=$4; CH=${5:-6}; ROOTS=${6:-/data/kalshi/ws,/data/kalshi/ws_archive}
mkdir -p results/raw results/state
python3 scripts/collect_results.py --root "$ROOTS" --out results/l2_results.json
python3 - "$START" "$END" "$CH" > results/state/${TAG}_chunks.txt <<'PY'
import sys, datetime as dt
s, e, ch = sys.argv[1], sys.argv[2], int(sys.argv[3])
f = lambda x: dt.datetime.strptime(x, "%Y%m%dT%H")
t = f(s)
while t <= f(e):
    u = min(t + dt.timedelta(hours=ch - 1), f(e))
    print(t.strftime("%Y%m%dT%H"), u.strftime("%Y%m%dT%H")); t = u + dt.timedelta(hours=1)
PY
PREV=""
while read -r A B; do
  OUT=results/raw/${TAG}_${A}_l2_rows.csv.gz
  ST=results/state/${TAG}_${B}.pkl
  if [ -s "$OUT" ] && [ -s "$ST" ]; then PREV=$ST; continue; fi     # resumable
  ARGS=(--root "$ROOTS" --prefix "$PREF" --start "$A" --end "$B" --results results/l2_results.json --out "$OUT" --state-out "$ST")
  if [ -n "$PREV" ]; then ARGS+=(--state-in "$PREV"); fi
  echo "== chunk $A..$B"
  python3 scripts/run_l2.py "${ARGS[@]}" > results/state/${TAG}_${A}.log 2>&1 || { echo "chunk $A failed, see log"; exit 1; }
  PREV=$ST
done < results/state/${TAG}_chunks.txt
echo "done; combine with: python3 scripts/combine_l2.py results/raw/${TAG}_*_l2_rows.csv.gz"
