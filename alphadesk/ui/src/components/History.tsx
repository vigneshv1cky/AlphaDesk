import { useState } from "react"
import { groupByDayKey, type SymbolTimeline, type TimelineEvent } from "@/lib/api"
import { dirUp, dirWord } from "@/lib/plain"
import { Badge } from "@/components/ui/badge"
import { Card, CardContent } from "@/components/ui/card"
import { InfoTip } from "@/components/InfoTip"
import { Skeleton } from "@/components/ui/skeleton"
import { Separator } from "@/components/ui/separator"
import { Table, TableHeader, TableBody, TableRow, TableHead, TableCell } from "@/components/ui/table"
import { TrendingUp, TrendingDown, Minus } from "lucide-react"
import { pnlClass, fmtPct } from "@/lib/pnl"

const SESSIONS = [
  { value: "ALL", label: "All" },
  { value: "PRE", label: "Pre-Market" },
  { value: "OPEN", label: "Open Market" },
  { value: "AFTER", label: "After Hours" },
] as const

// A pre-earnings pick bets momentum carries into a REPORT THAT HASN'T HAPPENED
// YET — real earnings-surprise risk a post-earnings-drift pick (the common
// case, already knows the reaction) doesn't carry. Flag only the exception so
// the common case stays unmarked.
function EdgeTag({ edge }: { edge: string | null }) {
  if (edge !== "PRE_EARNINGS") return null
  return (
    <InfoTip
      tip="Pre-earnings momentum bet — placed before the report, not a reaction to one"
      className="cursor-help"
    >
      <Badge variant="outline" className="ml-1 text-amber-600 dark:text-amber-400">pre-earnings</Badge>
    </InfoTip>
  )
}

function StatCard({ label, value, tone }: { label: string; value: string; tone?: number | null }) {
  const Icon = tone == null ? Minus : tone > 0 ? TrendingUp : TrendingDown
  const color = pnlClass(tone) || "text-muted-foreground"
  return <Card><CardContent className="flex flex-col items-center gap-1 py-4"><Icon className={`h-4 w-4 ${color}`} /><div className="text-[10px] font-medium uppercase tracking-widest text-muted-foreground">{label}</div><div className={`font-mono text-xl font-bold tabular-nums ${color}`}>{value}</div></CardContent></Card>
}

function fmtTs(ts: string | null) {
  if (!ts) return "—"
  const d = new Date(ts)
  if (isNaN(d.getTime())) return "—"
  return d.toLocaleDateString("en-US", { month: "short", day: "numeric" }) + " " + d.toLocaleTimeString("en-US", { hour: "2-digit", minute: "2-digit" })
}

function fmtFill(price: number | null, ts: string | null) {
  return (
    <div className="flex flex-col items-end">
      <span>{price != null ? `$${price.toFixed(2)}` : "—"}</span>
      <span className="text-[10px] text-muted-foreground">{fmtTs(ts)}</span>
    </div>
  )
}

type ExitedEvent = TimelineEvent & { symbol: string }

export function History({ symbols, loading }: { symbols: SymbolTimeline[]; loading: boolean }) {
  const [session, setSession] = useState<(typeof SESSIONS)[number]["value"]>("ALL")

  if (loading) return (
    <div className="space-y-3">
      <div className="grid grid-cols-3 gap-2">{[1,2,3].map(i => <Skeleton key={i} className="h-24 w-full rounded-xl" />)}</div>
      <Skeleton className="h-8 w-48" />{[1,2,3].map(i => <Skeleton key={i} className="h-10 w-full" />)}
    </div>
  )

  const allExited: ExitedEvent[] = []
  for (const s of symbols) for (const e of s.events) if (e.state === "exited" || e.state === "graded" || e.exit_ts) allExited.push({ ...e, symbol: s.symbol })
  const exitedEvents = session === "ALL" ? allExited : allExited.filter(e => e.session === session)
  const wins = exitedEvents.filter(e => (e.exit_return_pct ?? 0) > 0).length
  const winRate = exitedEvents.length > 0 ? Math.round((wins / exitedEvents.length) * 100) : null
  const totalPnl = exitedEvents.reduce((s, e) => s + (e.exit_return_pct ?? 0), 0)

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap gap-1">
        {SESSIONS.map(s => (
          <button
            key={s.value}
            onClick={() => setSession(s.value)}
            className={`rounded-md px-2.5 py-1 text-xs font-medium transition-colors ${
              session === s.value
                ? "bg-indigo-600 text-white"
                : "text-muted-foreground hover:bg-muted hover:text-foreground"
            }`}
          >
            {s.label}
          </button>
        ))}
      </div>
      <div className="grid grid-cols-2 gap-2">
        <StatCard label="Win Rate" value={winRate != null ? `${winRate}%` : "—"} tone={winRate != null ? (winRate >= 50 ? 1 : -1) : null} />
        <StatCard label="Total P&L" value={exitedEvents.length > 0 ? fmtPct(totalPnl) : "—"} tone={totalPnl} />
      </div>
      <Separator />
      {exitedEvents.length === 0 ? (
        <Card className="border-dashed"><CardContent className="flex flex-col items-center py-10"><p className="text-sm font-semibold">No closed positions yet</p><p className="mt-1 text-xs text-muted-foreground">Positions are graded at their 1-day horizon.</p></CardContent></Card>
      ) : (
        groupByDayKey(exitedEvents, e => e.ts).map(g => (
          <div key={g.key} className="space-y-2">
            <div className="flex items-center gap-2">
              <span className="text-xs font-bold uppercase tracking-wider text-muted-foreground">{g.label}</span>
              <Badge variant="secondary" className="text-[10px]">{g.items.length}</Badge>
            </div>
            <Table><TableHeader><TableRow>
              <TableHead>Symbol</TableHead><TableHead>Dir</TableHead>
              <TableHead className="text-right">Entry</TableHead><TableHead className="text-right">Exit</TableHead>
              <TableHead className="text-right">P&L</TableHead>
            </TableRow></TableHeader><TableBody>
              {g.items.map(e => {
                const up = dirUp(e.direction)
                const ret = e.exit_return_pct
                return (<TableRow key={e.id}>
                  <TableCell className="font-bold">{e.symbol}<EdgeTag edge={e.edge} /></TableCell>
                  <TableCell><Badge variant={up ? "default" : "destructive"} className="font-medium">{dirWord(e.direction)}</Badge></TableCell>
                  <TableCell className="text-right text-xs font-mono text-muted-foreground">{fmtFill(e.entry_price, e.entry_ts)}</TableCell>
                  <TableCell className="text-right text-xs font-mono text-muted-foreground">{fmtFill(e.exit_price, e.exit_ts)}</TableCell>
                  <TableCell className={`text-right font-mono tabular-nums font-semibold ${pnlClass(ret)}`}>{ret != null ? fmtPct(ret) : "—"}</TableCell>
                </TableRow>)
              })}
            </TableBody></Table>
          </div>
        ))
      )}
    </div>
  )
}
