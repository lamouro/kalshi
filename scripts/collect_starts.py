"""Collect event start times from trade-tape workflow journals into results/event_starts.json."""
import glob, json, os
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
out_p = os.path.join(ROOT, "results/event_starts.json")
out = json.load(open(out_p)) if os.path.exists(out_p) else {}
for j in glob.glob("/root/.claude/projects/-home-claude/*/subagents/workflows/*/journal.jsonl"):
    for l in open(j):
        d = json.loads(l)
        r = d.get("result") if d.get("type") == "result" else None
        if isinstance(r, dict) and "event" in r and "files" in r:
            out[r["event"]] = r.get("start_time")
json.dump(out, open(out_p, "w"), indent=1)
print(len(out), sum(v is not None for v in out.values()))
