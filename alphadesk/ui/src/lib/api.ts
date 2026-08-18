// AlphaDesk API client — same-origin; Basic Auth handled by the browser.

export interface Concern {
  claim: string
  evidence: string
}

export interface Rebuttal {
  rebuttal: string
  revised_score: number
  concede: boolean
}

export interface Debate {
  concerns?: Concern[]
  rebuttal?: Rebuttal
  fact_flags?: string[]
  arbiter_summary?: string
  critic_stance?: string
  counter_direction?: string
  counter?: string
  proposed_direction?: string
  final_direction?: string
  flipped?: boolean
}

export interface Brief {
  kind: string
  summary: string
  key_facts?: { fact: string }[]
}

// Actionable execution levels for a committed call (may be null if the desk
// couldn't set a coherent plan — the directional call still stands).
export interface Plan {
  entry: number
  target: number
  stop: number
  note: string
  hold: string // "single-day" | "multi-day"
}

// One open pick tracked live against the current price.

// One call in a symbol's timeline, with its outcome.

// The desk's evolving view on one stock — the "track record", grouped.

export interface TokenRow {
  role: string
  model: string
  calls: number
  input_tok: number
  output_tok: number
}

export interface EarningsRow {
  symbol: string
  report_date: string
  session: string | null
  eps_estimate: number | null
  eps_actual?: number | null
  surprise_pct?: number | null
  move_since_report_pct?: number | null // total reaction since the report (gap + drift)
  move_gap_pct?: number | null // the overnight/pre-market gap — repriced before you could act (uncapturable)
  move_drift_pct?: number | null // capturable drift from the first post-report open — what you could trade
  market_cap?: number | null // for ranking big names first within a run-day
  low_liquidity?: boolean | null // same 20d avg $vol bar the trading pipeline gates entries on; null = unmeasurable
  run_at?: string | null // when to run the desk to catch the drift (9:30 ET, next session)
  public_at?: string | null // when the report becomes tradeable (BMO/DAY 4:00, AMC 16:00 ET)
  pre_report_close?: number | null // pre-armed close the drift reaction is measured from
  implied_move_pct?: number | null // pre-armed options-implied move (the market's own expectation)
  // coverage self-assessment (reported names only): did the desk act on this reporter?
  engagement?: "TOOK" | "DEBATED" | "SKIPPED" | "UNSEEN"
  engagement_pick_id?: number | null
  engagement_dir?: "LONG" | "SHORT" | null
  engagement_verdict?: string | null // STRONG | SOFT | PASS (for took/debated)
  engagement_why?: string | null // the desk's own reason: judge summary / thesis / skip reason
}

// A pick as shown in the Sessions view (decision + lifecycle + outcome).
export interface SessionPick {
  id: number
  ts: string
  symbol: string
  direction: "LONG" | "SHORT"
  edge: string | null
  verdict: string | null
  approved: number
  adjusted_score: number | null
  confidence: number
  session: string
  horizon_days: number
  plan_entry: number | null
  plan_target: number | null
  plan_stop: number | null
  plan_note: string | null
  entry_price: number | null
  alpha_net: number | null
  graded_at: string | null
  exit_ts: string | null
  exit_reason: string | null
  exit_return_pct: number | null
}

export interface SessionAgg {
  n: number
  open: number
  graded: number
  wins: number
  avg_alpha: number | null
}

export interface SystemInfo {
  // last_run/runs_today/funnel_today are frozen dead fields — nothing has
  // written to those tables since the autonomous entry engine was deleted
  // 2026-08-16. Kept only so old API consumers don't break; not rendered.
  last_run: string | null
  runs_today: { total: number; with_picks: number; last_ts: string | null }
  funnel_today: { candidates: number; picked: number; skipped: number }
  open_positions: number
  graded: number
  exited: number
  uptime_s: number
  market: string
  news: {
    last_article_at: string | null
    articles_today: number
    tokens_today_in: number
    tokens_today_out: number
    calls_today: number
  }
}

async function get<T>(path: string): Promise<T> {
  const res = await fetch(path)
  if (!res.ok) throw new Error(`${path}: ${res.status}`)
  return res.json() as Promise<T>
}

// ── Human decision support (Phase 0) ────────────────────────────────────────

export interface ChartBar {
  t: string
  o: number
  h: number
  l: number
  c: number
}

/** OHLC + indicator series for the decision chart.
 *
 * `indicators_reliable` is NOT cosmetic. Alpaca's free IEX feed carries a few
 * percent of consolidated volume, so an illiquid name's "1-minute" series can
 * be a handful of prints stretched over days — and it renders identically to a
 * real one. Never draw RSI/MACD without surfacing this. */
export interface ChartSeries {
  symbol: string
  bars: ChartBar[]
  rsi_9: (number | null)[]
  macd: (number | null)[]
  macd_signal: (number | null)[]
  macd_hist: (number | null)[]
  thresholds: { rsi_oversold: number; rsi_overbought: number }
  bar_count: number
  sessions: number
  coverage: number
  median_gap_min: number | null
  indicators_reliable: boolean
}

async function post<T>(path: string, body: unknown): Promise<T> {
  const r = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  })
  if (!r.ok) {
    const detail = await r.json().catch(() => null)
    throw new Error(detail?.detail ?? `${r.status} ${r.statusText}`)
  }
  return r.json() as Promise<T>
}

/** One numbered item the answer cited, resolved server-side back to the
 * stored record — an article or a calendar row, never a URL the model made
 * up. `url` is empty for earnings citations (they came from our own
 * calendar, not a link). */
export interface ScreenerCitation {
  kind: "article" | "earnings"
  symbol: string
  claim: string
  title: string
  url: string
  source: string
}

export interface ScreenerHeadline {
  title: string
  url: string
  source: string
  published_at: string | null
}

/** One row of the window inventory. Deliberately carries NO score and NO
 * digest: the list is unranked and un-narrated, and the AI speaks only when
 * asked (see ScreenerAnswer). */
export interface ScreenerRow {
  symbol: string
  report_date: string | null
  session: string | null
  article_count: number
  headlines: ScreenerHeadline[]
}

/** The answer to one question asked of the whole window. `considered` is how
 * much the model was actually shown — worth surfacing, since it's the honest
 * scope of the answer. */
export interface ScreenerAnswer {
  answer: string
  citations: ScreenerCitation[]
  considered: { articles: number; earnings: number; symbols: number }
}

export interface FilingRow {
  accession: string
  symbol: string
  cik: string
  form: string
  filing_date: string
  report_date: string | null
  primary_doc: string
  url: string
}

export interface FilingCitation {
  quote: string
}

/** Answer to one question about one filing. Every citation is a VERBATIM
 * quote checked as an actual substring of the SEC document text server-side
 * — not the model's unverified say-so (see desk/filings.py). */
export interface FilingAnswer {
  answer: string
  citations: FilingCitation[]
}

/** One pre-fetched data section — the ground truth a citation resolves
 * against, not the model's own claim about what it read. */
export interface ResearchSection {
  title: string
  data: unknown
}

export interface ResearchCitation {
  section: number
  title: string
  claim: string
}

/** Answer from the research agent (desk/research.py) for one symbol. Every
 * citation points at a real, server-fetched entry in `sections`
 * (fundamentals, ownership, insider trades, earnings, macro, sector) —
 * fetched up front, then answered in a single AI call. */
export interface ResearchAnswer {
  answer: string
  citations: ResearchCitation[]
  sections: ResearchSection[]
}

export const api = {
  filings: (symbol: string) =>
    get<{ symbol: string; filings: FilingRow[] }>(`/api/filings/${encodeURIComponent(symbol)}`),
  askFiling: (accession: string, question: string) =>
    post<FilingAnswer>("/api/filings/ask", { accession, question }),
  askResearch: (symbol: string, question: string) =>
    post<ResearchAnswer>("/api/research/ask", { symbol, question }),
  chart: (symbol: string, days = 2) =>
    get<ChartSeries>(`/api/chart/${encodeURIComponent(symbol)}?days=${days}`),
  screener: () => get<{ symbols: ScreenerRow[] }>("/api/screener"),
  askScreener: (question: string) =>
    post<ScreenerAnswer>("/api/screener/ask", { question }),
  system: () => get<SystemInfo>("/api/system"),
  tokens: (days = 1) => get<{ usage: TokenRow[] }>(`/api/tokens?days=${days}`),
  earnings: () =>
    get<{ upcoming: EarningsRow[]; reported: EarningsRow[] }>("/api/earnings"),
}

// The market runs on US Eastern; show all decision timestamps there.
const ET = "America/New_York"

// "YYYY-MM-DD" in ET — used as a stable grouping key.
export function etDateKey(ts: string): string {
  return new Intl.DateTimeFormat("en-CA", {
    timeZone: ET,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(new Date(ts))
}

// "Tue 07-21" in ET — a compact day-group header.
export function etDayLabel(ts: string): string {
  const wd = new Intl.DateTimeFormat("en-US", { timeZone: ET, weekday: "short" }).format(
    new Date(ts),
  )
  return `${wd} ${etDateKey(ts).slice(5)}`
}

// Group items by their ET day (newest day first), preserving each item's incoming
// order within a day. `ts` picks the timestamp to group on.
export function groupByDayKey<T>(
  items: T[],
  ts: (x: T) => string,
): { key: string; label: string; items: T[] }[] {
  const map = new Map<string, T[]>()
  for (const it of items) {
    const k = etDateKey(ts(it))
    ;(map.get(k) ?? map.set(k, []).get(k)!).push(it)
  }
  return [...map.entries()]
    .sort((a, b) => (a[0] < b[0] ? 1 : -1))
    .map(([key, group]) => ({ key, label: etDayLabel(ts(group[0])), items: group }))
}

// "Jul 18, 14:23" in ET.
export function etDateTime(ts: string): string {
  return new Intl.DateTimeFormat("en-US", {
    timeZone: ET,
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
    hour12: true,
  }).format(new Date(ts))
}

export function fmtAlpha(a: number | null): string {
  if (a === null || a === undefined) return "…"
  return `${a > 0 ? "+" : ""}${a.toFixed(2)}%`
}

export function exitDate(ts: string, session: string, horizonDays: number): string {
  const d = new Date(ts)
  if (session === "CLOSED") d.setDate(d.getDate() + 1)   // only overnight picks wait for next open
  while (d.getDay() === 0 || d.getDay() === 6) d.setDate(d.getDate() + 1)
  let remaining = horizonDays
  while (remaining > 0) {
    d.setDate(d.getDate() + 1)
    if (d.getDay() !== 0 && d.getDay() !== 6) remaining -= 1
  }
  return d.toLocaleDateString("en-US", { weekday: "short", month: "short", day: "numeric" })
}
