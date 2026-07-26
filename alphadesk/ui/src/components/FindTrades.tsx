import { useRef, useState } from "react"
import { Button } from "@/components/ui/button"
import { Loader2, Search } from "lucide-react"
import { dirUp, dirWord, plainEdge, plainVerdict } from "@/lib/plain"
import type { Plan } from "@/lib/api"

// Streamed events (loosely typed — they arrive as JSON off the SSE feed).
interface Ev {
  type: string
  msg?: string
  symbol?: string
  edge?: string
  reason?: string
  kind?: string
  summary?: string
  direction?: string
  horizon_days?: number
  score?: number
  claim?: string
  evidence?: string
  text?: string
  revised_score?: number
  concede?: boolean
  id?: number
  conviction?: number
  confidence?: number
  verdict?: string
  approved?: boolean
  board?: BoardRow[]
  skips?: { symbol: string; reason: string }[]
  shock?: string
  strength?: string
  chain?: string
  entry?: number
  now?: number
  target?: number
  stop?: number
  hold?: string
  note?: string
  stance?: string
  counter_direction?: string
  counter?: string
  proposed_from?: string
  flipped?: boolean
}

interface BoardRow {
  id: number
  symbol: string
  direction: string
  horizon_days: number
  edge: string | null
  conviction: number
  confidence: number
  verdict: string
  approved: boolean
  summary: string
  take?: boolean
  chief_reason?: string
  flipped?: boolean
  plan?: Plan | null
}

function TermLine({ ev }: { ev: Ev }) {
  const tags: Record<string, [string, string]> = {
    triage_pick:    ["SCOUT",  "text-yellow-400"],
    gate:           ["GATE",   "text-zinc-500"],
    brief:          ["NOTE",   "text-zinc-400"],
    thesis:         ["THESIS", "text-blue-400"],
    concern:        ["CRITIC", "text-red-400"],
    counter:        ["CRITIC", "text-fuchsia-400"],
    fact_flag:      ["FACT",   "text-orange-400"],
    rebuttal:       ["REPLY",  "text-blue-400"],
    decision:       ["JUDGE",  "text-emerald-400"],
    plan:           ["PLAN",   "text-indigo-400"],
    exposure_shock: ["CHAIN",  "text-cyan-400"],
    exposure_candidate: ["CHAIN", "text-cyan-400"],
    debate_start:   ["DEBATE", "text-indigo-200"],
  }
  const [tag, color] = tags[ev.type] ?? ["EVENT", "text-zinc-500"]
  let body = ""
  switch (ev.type) {
    case "debate_start":
      body = `${ev.symbol} \u00b7 ${plainEdge(ev.edge)}`
      break
    case "triage_pick":
      body = `Shortlisted ${ev.symbol} \u00b7 ${plainEdge(ev.edge)} \u2014 ${ev.reason ?? ""}`
      break
    case "gate":
      body = `${ev.symbol} gated out: ${ev.reason ?? ""}`
      break
    case "brief":
      body = `${ev.symbol}: ${ev.summary ?? ""}`
      break
    case "thesis":
      body = `${dirWord(ev.direction)} ${ev.symbol} \u00b7 ${ev.score}/100 \u00b7 ${ev.horizon_days}d`
      break
    case "concern":
      body = `${ev.symbol}: ${ev.claim ?? ""}`
      break
    case "counter":
      body = ev.stance === "FLIP"
        ? `${ev.symbol} flip: ${ev.proposed_from} \u2192 ${ev.counter_direction}`
        : `${ev.symbol} stand aside`
      break
    case "fact_flag":
      body = ev.text ?? ""
      break
    case "rebuttal":
      body = `${ev.symbol}: score \u2192 ${ev.revised_score}/100 (concede: ${ev.concede ? "yes" : "no"})`
      break
    case "decision":
      body = `${ev.symbol} \u00b7 ${ev.approved ? "APPROVED" : "thin lean"} \u00b7 ${plainVerdict(ev.verdict)} \u00b7 ${ev.conviction}/100${ev.flipped ? " \u00b7 REVERSED by critic" : ""}`
      break
    case "plan":
      body = `${ev.symbol}: entry ${ev.entry} \u00b7 target ${ev.target} \u00b7 stop ${ev.stop}`
      break
    case "exposure_shock":
      body = `Looking for companies affected by ${ev.symbol}`
      break
    case "exposure_candidate":
      body = `${ev.shock} \u2192 ${ev.symbol} ${dirWord(ev.direction)} \u00b7 ${ev.strength}`
      break
    default:
      body = JSON.stringify(ev)
  }
  return (
    <div className="font-mono text-sm leading-relaxed">
      <span className={`font-semibold ${color}`}>[{tag}]</span>{" "}
      <span className="text-zinc-300">{body}</span>
    </div>
  )
}

export function FindTrades({
  onDone,
  onRunningChange,
}: {
  onDone: () => void
  onRunningChange?: (running: boolean) => void
}) {
  const [running, setRunning] = useState(false)
  const [status, setStatus] = useState("")
  const [feed, setFeed] = useState<Ev[]>([])
  const [board, setBoard] = useState<BoardRow[] | null>(null)
  const [chief, setChief] = useState("")
  const [positions, setPositions] = useState<Ev[]>([])
  const [deep, setDeep] = useState(false)
  const esRef = useRef<EventSource | null>(null)
  const termRef = useRef<HTMLDivElement | null>(null)

  function setRun(b: boolean) {
    setRunning(b)
    onRunningChange?.(b)
  }

  function run() {
    setRun(true)
    setFeed([])
    setBoard(null)
    setChief("")
    setPositions([])
    setStatus("Starting…")
    const es = new EventSource(`/api/find-trades?hours=24&max_debates=6&expose=${deep}`)
    esRef.current = es
    es.onmessage = (e) => {
      const ev: Ev = JSON.parse(e.data)
      if (ev.type === "status") setStatus(ev.msg ?? "")
      else if (ev.type === "chief") {
        setChief(ev.summary ?? "")
        setBoard(ev.board ?? [])
      } else if (ev.type === "done") {
        setBoard(ev.board ?? [])
        setRun(false)
        es.close()
        onDone()
      } else if (ev.type === "position_exit" || ev.type === "position_hold") {
        setPositions((p) => [...p, ev])
      } else {
        setFeed((f) => [...f, ev])
      }
    }
    es.onerror = () => {
      setStatus("stream closed")
      setRun(false)
      es.close()
    }
  }

  const takes = board?.filter((b) => b.take).length ?? 0

  return (
    <div className="overflow-hidden rounded-lg border bg-card text-card-foreground shadow-sm p-4">
        <div className="flex items-start justify-between gap-3">
          <div>
            <div className="flex items-center gap-2">
              <h2 className="text-base font-semibold tracking-tight">Find Trades</h2>
              {running && (
                <span className="flex items-center gap-1 text-[11px] font-medium text-emerald-600 dark:text-emerald-400">
                  <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-emerald-500" /> live
                </span>
              )}
            </div>
            <p className="mt-0.5 text-xs text-muted-foreground">
              Your research team scans the news and debates the best ideas, live.
            </p>
          </div>
          <Button
            onClick={run}
            disabled={running}
            size="lg"
            className="h-9 bg-indigo-600 px-4 text-sm text-white hover:bg-indigo-500"
          >
            {running ? (
              <>
                <Loader2 className="mr-1.5 h-4 w-4 animate-spin" /> Scanning…
              </>
            ) : (
              <>
                <Search className="mr-1.5 h-4 w-4" /> Run
              </>
            )}
          </Button>
        </div>

        <label className="mt-3 flex items-center gap-2 text-xs text-muted-foreground">
          <input
            type="checkbox"
            checked={deep}
            disabled={running}
            onChange={(e) => setDeep(e.target.checked)}
            className="accent-indigo-500"
          />
          Deep scan — also map supply-chain ripples (slower, uses more)
        </label>

        <details className="mt-3 text-xs text-muted-foreground">
          <summary className="cursor-pointer select-none">How this works</summary>
          <div className="mt-1.5 space-y-1.5 border-l-2 border-border pl-3">
            <p>
              <b className="text-foreground">1 · Scan</b> — every company that reported earnings
              (moved ≥1.5% on the print) + the last 12h of real news. Heavy earnings day? The
              news scan is skipped — reports are the best signal.
            </p>
            <p>
              <b className="text-foreground">2 · Review</b> — your open picks are re-checked
              first, on <i>fresh news only</i> (the reviewer never sees prices; a code watcher
              guards target/stop).
            </p>
            <p>
              <b className="text-foreground">3 · Scout</b> — one pass over all candidates picks
              the few worth a full debate, with a reason for every pick and every skip. A name
              debated in the last 24h is skipped unless genuinely new news landed.
            </p>
            <p>
              <b className="text-foreground">4 · Gate</b> — drops picks with no real, dated,
              externally-reported catalyst (rumors and price-chatter die here; earnings reports
              pass automatically).
            </p>
            <p>
              <b className="text-foreground">5 · Evidence</b> — facts fetched in code, never
              narrated from memory: price/volume/valuation, the options-implied move, earnings
              track record + analyst revisions, and supply-chain links with filing citations
              (deep scan).
            </p>
            <p>
              <b className="text-foreground">6 · Debate</b> — a{" "}
              <b className="text-foreground">researcher</b> argues the case, a{" "}
              <b className="text-foreground">critic</b> attacks it (and can flip the call), code
              fact-checks the numbers, the researcher replies, and a{" "}
              <b className="text-foreground">judge</b> commits: LONG or SHORT with conviction.
            </p>
            <p>
              <b className="text-foreground">7 · Plan</b> — entry at the current price, a
              realistic target, and a stop that invalidates the idea.
            </p>
            <p>
              <b className="text-foreground">8 · Head</b> — ranks the slate head-to-head; at
              most 2 picks per sector+direction per day (no stacked bets).
            </p>
            <p>
              <b className="text-foreground">9 · Aftermath</b> — every 3 minutes, code walks the
              price bars and closes at the first target/stop touched. Later, every call is
              graded vs the S&amp;P 500 at its own horizon — including the skips and rejects.
            </p>
            <p className="pt-1">
              These are <b className="text-foreground">research ideas, not trades</b> — nothing is
              bought. The scoreboard, not the AI&apos;s confidence, decides if any of it works.
            </p>
          </div>
        </details>

        {status && (
          <div className="mt-3 flex items-center gap-2 rounded-md bg-muted/50 px-3 py-2 text-sm text-muted-foreground">
            {running && <Loader2 className="h-3.5 w-3.5 shrink-0 animate-spin" />}
            <span>{status}</span>
          </div>
        )}

        {(running || feed.length > 0 || positions.length > 0 || board) && (
          <div className="mt-3 overflow-hidden rounded-lg border border-zinc-700 bg-zinc-950">
            <div
              ref={termRef}
              className="no-scrollbar max-h-[600px] overflow-y-auto px-4 py-3 font-mono text-sm leading-relaxed space-y-3"
            >
              {/* ── POSITION REVIEWS ── */}
              {positions.length > 0 && (
                <div>
                  <div className="text-zinc-500 mb-1">
                    ── Open Picks Review ({positions.filter((p) => p.type === "position_exit").length} to exit) ──
                  </div>
                  {positions.map((p, i) => {
                    const exit = p.type === "position_exit"
                    return (
                      <div key={i}>
                        <span className={`font-semibold ${exit ? "text-red-400" : "text-emerald-400"}`}>
                          [{exit ? "EXIT" : "HOLD"}]
                        </span>{" "}
                        <span className={dirUp(p.direction) ? "text-emerald-300" : "text-red-300"}>
                          {dirWord(p.direction)}
                        </span>{" "}
                        <span className="text-zinc-200 font-semibold">{p.symbol}</span>
                        <span className="text-zinc-600"> · ~{p.horizon_days}d</span>
                        {exit && p.entry != null && p.now != null && (
                          <span className="text-zinc-600"> · {p.entry} → {p.now}</span>
                        )}
                        <div className="text-zinc-500 ml-4">{p.reason}</div>
                      </div>
                    )
                  })}
                </div>
              )}

              {/* ── PIPELINE ── */}
              {(() => {
                const pre = feed.filter((e) => e.type === "triage_pick" || e.type === "gate"
                  || e.type === "exposure_shock" || e.type === "exposure_candidate")
                if (!pre.length) return null
                return (
                  <div>
                    <div className="border-t border-zinc-800 pt-3" />
                    <div className="text-zinc-500 mb-1">── Pipeline ──</div>
                    {pre.map((ev, i) => <TermLine key={i} ev={ev} />)}
                  </div>
                )
              })()}

              {/* ── HEAD / BOARD ── */}
              {board && (
                <div>
                  <div className="border-t border-zinc-800 pt-3" />
                  <div className="text-zinc-500 mb-1">
                    ── Head Ranking ({takes} suggested) ──
                  </div>
                  {chief && (
                    <div className="text-amber-400/80 mb-1">{chief}</div>
                  )}
                  {board.map((r, i) => (
                    <div key={r.id}>
                      <span className="text-zinc-600">#{i + 1}</span>{" "}
                      <span className={`font-semibold ${r.take ? "text-emerald-400" : "text-zinc-500"}`}>
                        [{r.take ? "TAKE" : "SKIP"}]
                      </span>{" "}
                      <span className={dirUp(r.direction) ? "text-emerald-300" : "text-red-300"}>
                        {dirWord(r.direction)}
                      </span>{" "}
                      <span className="text-zinc-200 font-semibold">{r.symbol}</span>
                      <span className="text-zinc-600"> · {plainEdge(r.edge)}</span>
                      <span className="text-zinc-600"> · conf {r.conviction}</span>
                      <span className="text-zinc-600"> · ~{r.horizon_days}d</span>
                      {r.flipped && <span className="text-fuchsia-400"> · reversed</span>}
                      {r.plan && (
                        <span className="text-zinc-600">
                          {" "}· entry {r.plan.entry} · target {r.plan.target} · stop {r.plan.stop}
                        </span>
                      )}
                      {r.summary && (
                        <div className="text-zinc-500 ml-4">{r.summary}</div>
                      )}
                      {r.chief_reason && (
                        <div className="text-amber-400/70 ml-4">{r.chief_reason}</div>
                      )}
                    </div>
                  ))}
                  {board.length === 0 && (
                    <div className="text-zinc-500">Nothing worth acting on.</div>
                  )}
                </div>
              )}

              {/* ── DEBATE FEED ── */}
              {(() => {
                const debateEvents = feed.filter((e) =>
                  e.type === "debate_start" || e.type === "brief" || e.type === "thesis"
                  || e.type === "concern" || e.type === "counter" || e.type === "fact_flag"
                  || e.type === "rebuttal" || e.type === "decision" || e.type === "plan"
                )
                if (!debateEvents.length) return null
                const headers: Record<string, Ev> = {}
                const items: Record<string, Ev[]> = {}
                const flippedSyms: Set<string> = new Set()
                for (const ev of debateEvents) {
                  const sym = ev.symbol ?? ""
                  if (!sym) continue
                  if (ev.type === "debate_start") {
                    headers[sym] = ev
                    if (!items[sym]) items[sym] = []
                  } else {
                    if (!items[sym]) items[sym] = []
                    items[sym].push(ev)
                    if (ev.type === "decision" && ev.flipped) {
                      flippedSyms.add(sym)
                    }
                  }
                }
                const order = debateEvents
                  .filter((e) => e.type === "debate_start")
                  .map((e) => e.symbol ?? "")
                  .filter((s, i, a) => s && a.indexOf(s) === i)
                return (
                  <div>
                    <div className="border-t border-zinc-800 pt-3" />
                    <div className="text-zinc-500 mb-1">── Debates ──</div>
                    {order.map((sym) => (
                      <div key={sym}>
                        {headers[sym] && <TermLine ev={headers[sym]} />}
                        {(items[sym]?.length ?? 0) > 0 && (
                          <div className="border-l border-zinc-800 ml-1.5 pl-2.5">
                            {items[sym].map((ev, ei) => (
                              <div key={ei} className="flex items-baseline gap-1">
                                <TermLine ev={ev} />
                                {ev.type === "thesis" && flippedSyms.has(sym) && (
                                  <span className="text-fuchsia-400/70">(reversed)</span>
                                )}
                              </div>
                            ))}
                          </div>
                        )}
                      </div>
                    ))}
                  </div>
                )
              })()}

              {!running && feed.length === 0 && positions.length === 0 && !board && (
                <div className="text-zinc-500">No results this run.</div>
              )}
            </div>
          </div>
        )}
    </div>
  )
}
