import { groupByDayKey, type SymbolTimeline, type TimelineEvent, type Stats } from "@/lib/api"
import { dirUp, dirWord } from "@/lib/plain"
import { Badge } from "@/components/ui/badge"
import { Card, CardContent } from "@/components/ui/card"
import { Skeleton } from "@/components/ui/skeleton"
import { Separator } from "@/components/ui/separator"
import { Table, TableHeader, TableBody, TableRow, TableHead, TableCell } from "@/components/ui/table"
import { TrendingUp, TrendingDown, Minus } from "lucide-react"

function StatCard({ label, value, tone }: { label: string; value: string; tone?: number | null }) {
  const Icon = tone == null ? Minus : tone > 0 ? TrendingUp : TrendingDown
  const color = tone == null ? "text-muted-foreground" : tone > 0 ? "text-emerald-500" : "text-red-500"
  return <Card><CardContent className="flex flex-col items-center gap-1 py-4"><Icon className={`h-4 w-4 ${color}`} /><div className="text-[10px] font-medium uppercase tracking-widest text-muted-foreground">{label}</div><div className={`font-mono text-xl font-bold tabular-nums ${color}`}>{value}</div></CardContent></Card>
}

function fmtTs(ts: string | null) {
  if (!ts) return "—"
  const d = new Date(ts)
  if (isNaN(d.getTime())) return "—"
  return d.toLocaleDateString("en-US", { month: "short", day: "numeric" }) + " " + d.toLocaleTimeString("en-US", { hour: "2-digit", minute: "2-digit" })
}

type ExitedEvent = TimelineEvent & { symbol: string }

export function History({ symbols, stats, loading }: { symbols: SymbolTimeline[]; stats: Stats | null; loading: boolean }) {
  if (loading) return (
    <div className="space-y-3">
      <div className="grid grid-cols-3 gap-2">{[1,2,3].map(i => <Skeleton key={i} className="h-24 w-full rounded-xl" />)}</div>
      <Skeleton className="h-8 w-48" />{[1,2,3].map(i => <Skeleton key={i} className="h-10 w-full" />)}
    </div>
  )

  const exitedEvents: ExitedEvent[] = []
  for (const s of symbols) for (const e of s.events) if (e.state === "exited" || e.state === "graded" || e.exit_ts) exitedEvents.push({ ...e, symbol: s.symbol })
  const graded = stats?.total?.graded ?? 0
  const gradedWins = stats?.total?.wins ?? 0
  const winRate = graded > 0 ? Math.round((gradedWins / graded) * 100) : null
  const alpha = stats?.total?.avg_alpha_net
  // Compute P&L from displayed events, not stats API
  const totalPnl = exitedEvents.reduce((s, e) => s + (e.exit_return_pct ?? 0), 0)

  return (
    <div className="space-y-3">
      <div className="grid grid-cols-3 gap-2">
        <StatCard label="Win Rate" value={winRate != null ? `${winRate}%` : "—"} tone={winRate != null ? (winRate >= 50 ? 1 : -1) : null} />
        <StatCard label="Avg Alpha" value={alpha != null ? `${alpha >= 0 ? "+" : ""}${alpha.toFixed(2)}%` : "—"} tone={alpha} />
        <StatCard label="Total P&L" value={exitedEvents.length > 0 ? `${totalPnl >= 0 ? "+" : ""}${totalPnl.toFixed(2)}%` : "—"} tone={totalPnl} />
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
              <TableHead className="text-right">Alpha</TableHead><TableHead className="text-right">P&L</TableHead>
            </TableRow></TableHeader><TableBody>
              {g.items.map(e => {
                const up = dirUp(e.direction)
                const ret = e.exit_return_pct
                const alp = e.alpha_net
                return (<TableRow key={e.id}>
                  <TableCell className="font-bold">{e.symbol}</TableCell>
                  <TableCell><Badge variant={up ? "default" : "destructive"} className="font-medium">{dirWord(e.direction)}</Badge></TableCell>
                  <TableCell className="text-right text-xs font-mono text-muted-foreground">{fmtTs(e.entry_ts)}</TableCell>
                  <TableCell className="text-right text-xs font-mono text-muted-foreground">{fmtTs(e.exit_ts)}</TableCell>
                  <TableCell className={`text-right font-mono tabular-nums font-semibold ${alp != null && alp > 0 ? "text-emerald-500" : alp != null && alp < 0 ? "text-red-500" : ""}`}>{alp != null ? `${alp >= 0 ? "+" : ""}${alp.toFixed(2)}%` : "—"}</TableCell>
                  <TableCell className={`text-right font-mono tabular-nums font-semibold ${ret != null && ret > 0 ? "text-emerald-500" : ret != null && ret < 0 ? "text-red-500" : ""}`}>{ret != null ? `${ret >= 0 ? "+" : ""}${ret.toFixed(2)}%` : "—"}</TableCell>
                </TableRow>)
              })}
            </TableBody></Table>
          </div>
        ))
      )}
    </div>
  )
}
