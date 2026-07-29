import { useEffect, useState } from "react"
import { api, etDateTime, groupByDayKey, type SymbolTimeline, type TimelineEvent, type Stats } from "@/lib/api"
import { dirUp, dirWord } from "@/lib/plain"
import { ArrowDown, ArrowUp, RotateCcw } from "lucide-react"
import { Card } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"

function Stat({ label, value, sub, tone }: { label: string; value: string; sub?: string; tone?: number | null }) {
  const color = tone == null ? "" : tone > 0 ? "text-emerald-600 dark:text-emerald-400" : tone < 0 ? "text-red-600 dark:text-red-400" : ""
  return (
    <Card size="sm">
      <div className="text-[10px] uppercase tracking-wider text-muted-foreground">{label}</div>
      <div className={`mt-0.5 font-mono text-lg font-semibold tabular-nums ${color}`}>{value}</div>
      {sub && <div className="text-[11px] text-muted-foreground">{sub}</div>}
    </Card>
  )
}

function PerfStrip({ stats }: { stats: Stats | null }) {
  const graded = stats?.total.graded ?? 0
  const wins = stats?.total.wins ?? 0
  const winRate = graded > 0 ? Math.round((wins / graded) * 100) : null
  const pnl = stats?.total.total_return_pct ?? null
  const exited = stats?.total.exited ?? 0
  const scoredSub =
    winRate != null ? `${winRate}% win` : "grading forward"
  return (
    <div className="grid grid-cols-3 gap-2">
      <Stat label="Ideas logged" value={String(stats?.total.picks ?? 0)} />
      <Stat label="Scored" value={String(graded)} sub={scoredSub} />
      <Stat
        label="Profit & Loss"
        value={pnl != null ? `${pnl >= 0 ? "+" : ""}${pnl}%` : "—"}
        tone={pnl}
        sub={`${exited} closed`}
      />
    </div>
  )
}

// Classify how a position was closed, from the recorded exit reason + realized
// alpha. The watcher writes deterministic "target hit …" / "stopped out …"
// reasons; the review agent writes a free-text thesis close. A target hit is a
// win (green), a stop a loss (red); a discretionary close is toned by what it
// actually banked (a small give-back reads red, a locked-in gain green).
function exitKind(
  reason: string | null | undefined,
  realized: number | null | undefined,
): { label: string; tone: number } {
  const r = (reason ?? "").toLowerCase()
  if (r.startsWith("target hit")) return { label: "target hit", tone: 1 }
  if (r.startsWith("stopped out")) return { label: "stopped out", tone: -1 }
  return { label: "closed early", tone: realized ?? 0 }
}

// Green for a gain, red for a loss, amber for a flat/unknown close.
function toneText(t: number): string {
  return t > 0
    ? "text-emerald-600 dark:text-emerald-400"
    : t < 0
      ? "text-red-600 dark:text-red-400"
      : "text-amber-600 dark:text-amber-400"
}
function toneChip(t: number): string {
  return t > 0
    ? "bg-emerald-500/15 text-emerald-600 dark:text-emerald-400"
    : t < 0
      ? "bg-red-500/15 text-red-600 dark:text-red-400"
      : "bg-amber-500/15 text-amber-600 dark:text-amber-400"
}

// The desk's current stance on a stock — the headline of its timeline card. For
// an exited name the badge says WHY it closed and is colored by the outcome.
function StanceBadge({ current, exit }: { current: string; exit?: { label: string; tone: number } | null }) {
  if (current === "EXITED" && exit) {
    return <Badge className={`text-[11px] font-semibold ${toneChip(exit.tone)}`}>Exited · {exit.label}</Badge>
  }
  const map: Record<string, { label: string; cls: string }> = {
    LONG: { label: "Buy", cls: "bg-emerald-500/15 text-emerald-600 dark:text-emerald-400" },
    SHORT: { label: "Short", cls: "bg-red-500/15 text-red-600 dark:text-red-400" },
    EXITED: { label: "Exited", cls: "bg-amber-500/15 text-amber-600 dark:text-amber-400" },
    NOT_TAKEN: { label: "Not taken", cls: "bg-muted text-muted-foreground" },
    CLOSED: { label: "Closed", cls: "bg-muted text-muted-foreground" },
  }
  const s = map[current] ?? map.CLOSED
  return <Badge className={`text-[11px] font-semibold ${s.cls}`}>{s.label}</Badge>
}

// What happened with one call: exited, or live P&L (open).
function Outcome({ e }: { e: TimelineEvent }) {
  if (e.state === "not_taken") {
    // thesis died before the open fill — never held, so no realized P&L (still
    // graded for direction at its horizon).
    return <span className="text-xs font-medium text-muted-foreground">Not taken</span>
  }
  if (e.state === "open" && e.status === "pending") {
    // decided while the market was shut — fills at the next open, not a position yet.
    return <span className="text-xs font-medium text-amber-600 dark:text-amber-400">Pending open</span>
  }
  if (e.state === "exited") {
    const ret = e.exit_return_pct
    if (ret == null) return <span className="text-xs font-semibold text-muted-foreground">Exited</span>
    return (
      <span className={`font-mono text-sm font-semibold tabular-nums ${ret > 0 ? "text-emerald-600 dark:text-emerald-400" : "text-red-600 dark:text-red-400"}`}>
        {ret >= 0 ? "+" : ""}{ret.toFixed(2)}%
      </span>
    )
  }
  if (e.pnl_pct != null) {
    const pos = e.pnl_pct >= 0
    return (
      <span className="text-right">
        <span className="font-mono text-sm tabular-nums">${e.current}</span>{" "}
        <span className={`font-mono text-xs tabular-nums ${pos ? "text-emerald-600 dark:text-emerald-400" : "text-red-600 dark:text-red-400"}`}>
          ({pos ? "+" : ""}
          {e.pnl_pct}%)
        </span>
      </span>
    )
  }
  return <span className="text-xs text-muted-foreground">scoring…</span>
}

function EventRow({ e, onSelect }: { e: TimelineEvent; onSelect: (id: number) => void }) {
  const up = dirUp(e.direction)
  return (
    <button
      onClick={() => onSelect(e.id)}
      className="flex w-full items-center gap-2.5 rounded-md px-2 py-2 text-left transition-colors hover:bg-muted/50"
    >
      {up ? <ArrowUp className="h-4 w-4 shrink-0 text-emerald-600 dark:text-emerald-400" /> : <ArrowDown className="h-4 w-4 shrink-0 text-red-600 dark:text-red-400" />}
      <span className={`text-sm font-semibold ${up ? "text-emerald-600 dark:text-emerald-400" : "text-red-600 dark:text-red-400"}`}>{dirWord(e.direction)}</span>
      <span className="font-mono text-xs tabular-nums text-muted-foreground">
        {e.state === "not_taken"
          ? <span className="text-muted-foreground/70">passed · {etDateTime(e.entry_ts)}</span>
          : <>{etDateTime(e.entry_ts)}
              {e.exit_ts && (
                <span className="text-muted-foreground/70"> → {etDateTime(e.exit_ts)}</span>
              )}
            </>}
      </span>
      <span className="ml-auto shrink-0">
        <Outcome e={e} />
      </span>
    </button>
  )
}

function SymbolCard({ s, onSelect }: { s: SymbolTimeline; onSelect: (id: number) => void }) {
  const events = [...s.events].reverse() // newest first
  const shown = events.slice(0, 8)
  const more = events.length - shown.length
  const latest = events[0]
  const notTaken = s.current === "NOT_TAKEN"
  const exitReason = s.current === "EXITED" || notTaken ? latest?.exit_reason : null
  const exit =
    s.current === "EXITED" && latest
      ? exitKind(latest.exit_reason, latest.exit_alpha ?? latest.exit_return_pct)
      : null
  return (
    <Card>
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-base font-bold">{s.symbol}</span>
        <StanceBadge current={s.current} exit={exit} />
        {s.changed && s.current !== "EXITED" && !notTaken && (
          <Badge className="gap-1 bg-fuchsia-500/15 font-semibold text-fuchsia-600 dark:text-fuchsia-400">
            <RotateCcw className="h-2.5 w-2.5" /> changed
          </Badge>
        )}
        <span className="ml-auto text-xs text-muted-foreground">
          {events.length} call{events.length > 1 ? "s" : ""}
        </span>
      </div>
      {exitReason &&
        (notTaken ? (
          <p className="mt-1.5 text-xs italic text-muted-foreground">{exitReason}</p>
        ) : (
          <p className={`mt-1.5 text-xs ${toneText(exit?.tone ?? 0)}`}>Exited: {exitReason}</p>
        ))}
      <div className="mt-2 divide-y divide-border/60">
        {shown.map((e) => (
          <EventRow key={e.id} e={e} onSelect={onSelect} />
        ))}
      </div>
      {more > 0 && <div className="mt-1.5 px-2 text-xs text-muted-foreground">+{more} earlier</div>}
    </Card>
  )
}

export function Ledger({ stats, onSelect }: { stats: Stats | null; onSelect: (id: number) => void }) {
  const [symbols, setSymbols] = useState<SymbolTimeline[]>([])
  const [loaded, setLoaded] = useState(false)
  const [showNotTaken, setShowNotTaken] = useState(false)

  useEffect(() => {
    let alive = true
    const load = () =>
      api
        .timelines()
        .then((d) => {
          if (alive) {
            setSymbols(d.symbols)
            setLoaded(true)
          }
        })
        .catch(console.error)
    load()
    const t = setInterval(load, 30_000) // outcomes update live for open calls
    return () => {
      alive = false
      clearInterval(t)
    }
  }, [])

  return (
    <div className="space-y-3">
      <PerfStrip stats={stats} />
      {loaded && symbols.length === 0 ? (
        <Card className="border-dashed p-8 text-center">
          <p className="text-sm font-medium">No ideas yet</p>
          <p className="mx-auto mt-1 max-w-xs text-xs text-muted-foreground">
            Hit <b className="text-foreground">Run</b> on the desk. Each stock builds a timeline here —
            every call, whether it worked, and when the desk changed its mind.
          </p>
        </Card>
      ) : (
        <div className="space-y-3">
          <label className="flex items-center gap-2 text-xs text-muted-foreground">
            <input type="checkbox" checked={showNotTaken} onChange={e => setShowNotTaken(e.target.checked)}
              className="accent-indigo-500" />
            Show not taken
          </label>
          {groupByDayKey(
            showNotTaken ? symbols : symbols.filter(s => s.current !== "NOT_TAKEN"),
            (s) => s.last_ts
          ).map((g) => (
            <div key={g.key} className="space-y-2">
              <div className="text-xs font-semibold text-indigo-600 dark:text-indigo-400">
                {g.label}
              </div>
              {g.items.map((s) => (
                <SymbolCard key={s.symbol} s={s} onSelect={onSelect} />
              ))}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
