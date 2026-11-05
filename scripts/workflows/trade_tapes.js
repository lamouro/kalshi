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

STEP 2 - for EACH strike pull two windows with GET /markets/trades?ticker=TICKER&min_ts=UNIX&max_ts=UNIX&limit=200 (results come newest first; follow the cursor):
  window OPEN: from the market open time to open + 6 hours.
  window PRE: if a start exists, from start - 24 h to start + 3 h; otherwise from close_time - 48 h to close_time.
  Stop a window after 3 pages (600 trades) and mark it truncated.
Write one CSV per strike and window to ${OUT}/TICKER__open.csv and ${OUT}/TICKER__pre.csv with header created_time,yes_price,count,taker_book_side , oldest first. Use Bash mkdir -p ${OUT} first. /mnt/project-files is a network mount: write each file once, then re-read it after ~10 s to confirm it is there and complete.

Do not invent or interpolate any trade. If a page fails twice, record the failure. Return the start time found, and per file: rows, truncated yes/no, first and last timestamps.`
const SCHEMA = { type: 'object', properties: {
  event: { type: 'string' }, start_time: { type: ['string', 'null'] }, start_source: { type: 'string' },
  files: { type: 'array', items: { type: 'object', properties: { file: { type: 'string' }, rows: { type: 'number' }, truncated: { type: 'boolean' }, first_ts: { type: 'string' }, last_ts: { type: 'string' } }, required: ['file', 'rows'] } },
  failures: { type: 'array', items: { type: 'string' } } }, required: ['event', 'files'] }
phase('Pull')
return await pipeline(args, j => agent(PROMPT(j), { label: `tape:${j.event}`, phase: 'Pull', schema: SCHEMA, agentType: 'general-purpose' }))
