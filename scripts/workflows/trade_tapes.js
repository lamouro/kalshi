export const meta = {
  name: 'kalshi-trade-tapes',
  description: 'Pull Kalshi public trade tapes (windows around open and around the event) for chosen markets via WebFetch',
  phases: [{ title: 'Pull', detail: 'one agent per event' }],
}
const OUT = '/mnt/project-files/kalshi-data/trades'
const PROMPT = (j) => `You are a data collector for a paper-trading backtest. The shell cannot reach Kalshi (proxy 403); use the WebFetch tool on Kalshi's PUBLIC REST API, base https://api.elections.kalshi.com/trade-api/v2 . WebFetch passes the body through a small model, so ALWAYS ask it to "transcribe every trade in the JSON verbatim, one per line, as created_time,yes_price_dollars,count_fp,taker_book_side - no summary, no omissions, then print the cursor value". (If taker_book_side is missing, use taker_side and note it.) Check each page: the number of lines should equal the number of trades in the page; if WebFetch summarises or truncates, retry with a smaller limit.

EVENT: ${j.event} (series ${j.series}); event open ${j.open_time}, last close ${j.close_time}.
STRIKES: ${j.strikes.map(s => s.ticker + ' (result ' + s.result + ', close ' + s.close_time + ')').join('; ')}

STEP 1 - event start time. Try GET /milestones?limit=100&related_event_ticker=${j.event} (or /milestones?event_ticker=..., or search /milestones?category=... ), and GET /events/${j.event} . Record the scheduled start (start_date) if any. For weekly/monthly word markets, Rotten Tomatoes or Dancing with the Stars there may be no single start: then use null.

${j.windows && j.windows.length === 1 ? 'ONLY pull the ' + j.windows[0].toUpperCase() + ' window below; skip the other window entirely (write no file for it).\n\n' : ''}STEP 2 - for EACH strike pull two windows with GET /markets/trades?ticker=TICKER&min_ts=UNIX&max_ts=UNIX&limit=200 (results come newest first; follow the cursor):
  window OPEN: the EARLIEST trades after the market open. Query min_ts=open, max_ts=open+48h. If that window needs more than 3 pages, shrink max_ts (halve the span, repeat) until the whole window fits in 3 pages or fewer, so the file holds the first trades after listing, complete from the open (never the newest ones).
  window PRE: if a start exists, from start - 24 h to start + 3 h; otherwise from close_time - 48 h to close_time.
  IMPORTANT: trades created before the historical cutoff (GET /historical/cutoff, trades_created_ts, about 2026-09-05) are ONLY served by GET /historical/trades?ticker=...&min_ts=...&max_ts=... ; /markets/trades returns nothing for them. Use the historical endpoint for any window (or part of a window) before the cutoff. Stop a window after 3 pages (600 trades) and mark it truncated.
Write one CSV per strike and window to ${OUT}/TICKER__open.csv and ${OUT}/TICKER__pre.csv with header created_time,yes_price,count,taker_book_side , oldest first. Use Bash mkdir -p ${OUT} first. /mnt/project-files is a network mount: write each file once, then re-read it after ~10 s to confirm it is there and complete.

RATE LIMIT: WebFetch has a shared budget of 400 calls per hour for all agents (one call refills every 9 s). Wait 20 s before EVERY WebFetch call (in Bash: python3 -c "import time; time.sleep(20)"). If a call is refused with a 403 about the budget, wait 120 s the same way (time.sleep(120)) and try again, up to 15 times, before giving up on that call. Do not give up on the whole event because of the budget. Do not invent or interpolate any trade. If a page fails twice, record the failure. Return the start time found, and per file: rows, truncated yes/no, first and last timestamps.`
const SCHEMA = { type: 'object', properties: {
  event: { type: 'string' }, start_time: { type: ['string', 'null'] }, start_source: { type: 'string' },
  files: { type: 'array', items: { type: 'object', properties: { file: { type: 'string' }, rows: { type: 'number' }, truncated: { type: 'boolean' }, first_ts: { type: 'string' }, last_ts: { type: 'string' } }, required: ['file', 'rows'] } },
  failures: { type: 'array', items: { type: 'string' } } }, required: ['event', 'files'] }
phase('Pull')
const items = Array.isArray(args) ? args : args.events.map(e => ({ event: e, jobfile: args.jobfile }))
const P = (j) => j.jobfile
  ? `FIRST read your job spec: the entry whose "event" is ${j.event} in the JSON file ${j.jobfile} (Bash: python3 -c "import json;print([e for e in json.load(open('${j.jobfile}')) if e['event']=='${j.event}'])"). It gives series, open_time, close_time, strikes (ticker, result, close_time) and, if present, "windows" (pull ONLY those windows). Then do the task below using those values.\n\n` + PROMPT({ ...j, series: '(see spec)', open_time: '(see spec)', close_time: '(see spec)', strikes: [{ ticker: '(see spec)', result: '', close_time: '' }] })
  : PROMPT(j)
return await pipeline(items, j => agent(P(j), { label: `tape:${j.event}`, phase: 'Pull', schema: SCHEMA, agentType: 'general-purpose' }))
