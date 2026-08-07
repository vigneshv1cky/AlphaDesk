import { Badge } from "@/components/ui/badge"
import { Card, CardContent } from "@/components/ui/card"
import { Skeleton } from "@/components/ui/skeleton"
import { Separator } from "@/components/ui/separator"
import { Table, TableHeader, TableBody, TableRow, TableHead, TableCell } from "@/components/ui/table"
import { dirUp, dirWord } from "@/lib/plain"
import { groupByDayKey, type LivePick, type SymbolTimeline, type TimelineEvent } from "@/lib/api"

// One market window = one trade (session-scoped model): enter at the session
// open, exit at the session close, never carry into another market. Night is not
// tradeable (market closed 20:00–4:00) — night-decided picks are stamped PRE
// because they enter at the next 4:00 open.
export const MARKET_SESSIONS = {
  PRE: { label: "Pre-Market", window: "4:00–9:30 ET" },
  OPEN: { label: "Open Market", window: "9:30–16:00 ET" },
  AFTER: { label: "After Hours", window: "16:00–20:00 ET" },
} as const

type SessionCode = keyof typeof MARKET_SESSIONS

const POSITION_USD = 10

// Same fractional-$10 P&L math as the Live table: each position is $10 of the name.
function pnlUsd(p: LivePick): number | null {
  if (p.current == null || p.plan_entry == null || p.plan_entry <= 0) return null
  const perShare = p.direction === "LONG" ? (p.current - p.plan_entry) : (p.plan_entry - p.current)
  return (POSITION_USD / p.plan_entry) * perShare
}

function fmtTs(ts: string | null) {
  if (!ts) return "—"
  const d = new Date(ts)
  if (isNaN(d.getTime())) return "—"
  return d.toLocaleDateString("en-US", { month: "short", day: "numeric" }) + " " + d.toLocaleTimeString("en-US", { hour: "2-digit", minute: "2-digit" })
}

function timeAgo(ts: string): string {
  const d = new Date(ts)
  if (isNaN(d.getTime())) return "—"
  const min = Math.floor((Date.now() - d.getTime()) / 60_000)
  if (min < 1) return "now"
  if (min < 60) return `${min}m`
  if (min < 60 * 24) return `${Math.floor(min / 60)}h`
  return `${Math.floor(min / (60 * 24))}d`
}

type Closed = TimelineEvent & { symbol: string }

export default function MarketPage({
  session,
  liveRows,
  symbols,
  loading,
}: {
  session: SessionCode
  liveRows: LivePick[]
  symbols: SymbolTimeline[]
  loading: boolean
}) {
  const meta = MARKET_SESSIONS[session]
  const live = liveRows.filter(p => p.session === session)
  const closed: Closed[] = []
  for (const s of symbols) {
    for (const e of s.events) {
      if (e.session !== session) continue
      if (e.state !== "exited" && e.state !== "graded") continue
      closed.push({ ...e, symbol: s.symbol })
    }
  }

  const graded = closed.filter(e => e.alpha_net != null)
  const wins = graded.filter(e => (e.alpha_net ?? 0) > 0).length
  const winRate = graded.length ? Math.round((wins / graded.length) * 100) : null
  const avgAlpha = graded.length ? graded.reduce((s, e) => s + (e.alpha_net ?? 0), 0) / graded.length : null
  const totalPnl = closed.reduce((s, e) => s + (e.exit_return_pct ?? 0), 0)

  if (loading) return (
    <div className="space-y-3">
      <Skeleton className="h-8 w-64" />
      <div className="grid grid-cols-4 gap-2">{[1, 2, 3, 4].map(i => <Skeleton key={i} className="h-24 w-full rounded-xl" />)}</div>
      {[1, 2, 3].map(i => <Skeleton key={i} className="h-10 w-full" />)}
    </div>
  )

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <h1 className="text-lg font-bold tracking-tight">{meta.label}</h1>
        <span className="text-xs text-muted-foreground">{meta.window} — session-scoped: enter at the open, exit at the close, never carry into another market.</span>
      </div>

      <div className="grid grid-cols-4 gap-2">
        <Card><CardContent className="flex flex-col items-center gap-1 py-3">
          <div className="text-[10px] font-medium uppercase tracking-widest text-muted-foreground">Open</div>
          <div className="font-mono text-lg font-bold tabular-nums">{live.length}</div>
          <div className="text-[10px] text-muted-foreground">positions now</div>
        </CardContent></Card>
        <Card><CardContent className="flex flex-col items-center gap-1 py-3">
          <div className="text-[10px] font-medium uppercase tracking-widest text-muted-foreground">Win Rate</div>
          <div className={`font-mono text-lg font-bold tabular-nums ${winRate != null ? (winRate >= 50 ? "text-emerald-500" : "text-red-500") : "text-muted-foreground"}`}>{winRate != null ? `${winRate}%` : "—"}</div>
          <div className="text-[10px] text-muted-foreground">{graded.length} graded</div>
        </CardContent></Card>
        <Card><CardContent className="flex flex-col items-center gap-1 py-3">
          <div className="text-[10px] font-medium uppercase tracking-widest text-muted-foreground">Avg Alpha</div>
          <div className={`font-mono text-lg font-bold tabular-nums ${avgAlpha != null ? (avgAlpha >= 0 ? "text-emerald-500" : "text-red-500") : "text-muted-foreground"}`}>{avgAlpha != null ? `${avgAlpha >= 0 ? "+" : ""}${avgAlpha.toFixed(2)}%` : "—"}</div>
          <div className="text-[10px] text-muted-foreground">vs SPY, net friction</div>
        </CardContent></Card>
        <Card><CardContent className="flex flex-col items-center gap-1 py-3">
          <div className="text-[10px] font-medium uppercase tracking-widest text-muted-foreground">Total P&L</div>
          <div className={`font-mono text-lg font-bold tabular-nums ${totalPnl >= 0 ? "text-emerald-500" : "text-red-500"}`}>{totalPnl >= 0 ? "+" : ""}{totalPnl.toFixed(2)}%</div>
          <div className="text-[10px] text-muted-foreground">{closed.length} closed</div>
        </CardContent></Card>
      </div>

      <div>
        <h2 className="mb-2 text-xs font-bold uppercase tracking-wider text-muted-foreground">Open positions — {meta.label}</h2>
        {live.length === 0 ? (
          <Card className="border-dashed"><CardContent className="flex flex-col items-center py-8">
            <p className="text-sm font-semibold">No open positions in this session</p>
            <p className="mt-1 text-xs text-muted-foreground">Waiting for the next {meta.label} run.</p>
          </CardContent></Card>
        ) : (
          <Table>
            <TableHeader><TableRow>
              <TableHead>Symbol</TableHead><TableHead>Dir</TableHead>
              <TableHead className="text-right">Entry</TableHead><TableHead className="text-right">Now</TableHead><TableHead className="text-right">P&L</TableHead>
              <TableHead className="text-right">Tgt</TableHead><TableHead className="text-right">Stop</TableHead>
              <TableHead className="text-right">Age</TableHead>
            </TableRow></TableHeader>
            <TableBody>{live.map(p => {
              const up = dirUp(p.direction)
              const pnl = pnlUsd(p)
              return (<TableRow key={p.id}>
                <TableCell className="font-bold">{p.symbol}</TableCell>
                <TableCell><Badge variant={up ? "default" : "destructive"} className="font-medium">{dirWord(p.direction)}</Badge></TableCell>
                <TableCell className="text-right font-mono tabular-nums">${p.plan_entry.toFixed(2)}</TableCell>
                <TableCell className="text-right font-mono tabular-nums">{p.current != null ? `$${p.current.toFixed(2)}` : "—"}</TableCell>
                <TableCell className={`text-right font-mono tabular-nums font-semibold ${(pnl ?? 0) >= 0 ? "text-emerald-500" : "text-red-500"}`}>{pnl != null ? `${pnl >= 0 ? "+" : ""}$${pnl.toFixed(2)}` : "—"}</TableCell>
                <TableCell className="text-right font-mono tabular-nums">${p.plan_target.toFixed(2)}</TableCell>
                <TableCell className="text-right font-mono tabular-nums">${p.plan_stop.toFixed(2)}</TableCell>
                <TableCell className="text-right text-xs text-muted-foreground">{timeAgo(p.entry_ts)}</TableCell>
              </TableRow>)
            })}</TableBody>
          </Table>
        )}
      </div>

      <Separator />

      <div>
        <h2 className="mb-2 text-xs font-bold uppercase tracking-wider text-muted-foreground">Closed trades — {meta.label}</h2>
        {closed.length === 0 ? (
          <Card className="border-dashed"><CardContent className="flex flex-col items-center py-8">
            <p className="text-sm font-semibold">No closed trades in this session yet</p>
            <p className="mt-1 text-xs text-muted-foreground">Positions exit at the {meta.label} session close.</p>
          </CardContent></Card>
        ) : (
          <div className="space-y-2">
            {groupByDayKey(closed, e => e.ts).map(g => (
              <div key={g.key} className="space-y-2">
                <div className="flex items-center gap-2">
                  <span className="text-xs font-bold uppercase tracking-wider text-muted-foreground">{g.label}</span>
                  <Badge variant="secondary" className="text-[10px]">{g.items.length}</Badge>
                </div>
                <Table><TableHeader><TableRow>
                  <TableHead>Symbol</TableHead><TableHead>Dir</TableHead>
                  <TableHead className="text-right">Entry</TableHead><TableHead className="text-right">Exit</TableHead>
                  <TableHead className="text-right">Alpha</TableHead><TableHead className="text-right">P&L</TableHead>
                </TableRow></TableHeader><TableBody>
                  {g.items.map(e => {
                    const up = dirUp(e.direction)
                    return (<TableRow key={e.id}>
                      <TableCell className="font-bold">{e.symbol}</TableCell>
                      <TableCell><Badge variant={up ? "default" : "destructive"} className="font-medium">{dirWord(e.direction)}</Badge></TableCell>
                      <TableCell className="text-right text-xs font-mono text-muted-foreground">{fmtTs(e.entry_ts)}</TableCell>
                      <TableCell className="text-right text-xs font-mono text-muted-foreground">{fmtTs(e.exit_ts)}</TableCell>
                      <TableCell className={`text-right font-mono tabular-nums font-semibold ${(e.alpha_net ?? 0) > 0 ? "text-emerald-500" : (e.alpha_net ?? 0) < 0 ? "text-red-500" : ""}`}>{e.alpha_net != null ? `${e.alpha_net >= 0 ? "+" : ""}${e.alpha_net.toFixed(2)}%` : "—"}</TableCell>
                      <TableCell className={`text-right font-mono tabular-nums font-semibold ${(e.exit_return_pct ?? 0) > 0 ? "text-emerald-500" : (e.exit_return_pct ?? 0) < 0 ? "text-red-500" : ""}`}>{e.exit_return_pct != null ? `${e.exit_return_pct >= 0 ? "+" : ""}${e.exit_return_pct.toFixed(2)}%` : "—"}</TableCell>
                    </TableRow>)
                  })}
                </TableBody></Table>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
