import { Skeleton } from "@/components/ui/skeleton"
import { Badge } from "@/components/ui/badge"
import { Card, CardContent } from "@/components/ui/card"
import { Separator } from "@/components/ui/separator"
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip"
import { Table, TableHeader, TableBody, TableRow, TableHead, TableCell } from "@/components/ui/table"
import { RefreshCw, Target, ShieldAlert, TrendingUp, Activity } from "lucide-react"
import { dirUp, dirWord } from "@/lib/plain"
import type { LivePick } from "@/lib/api"

function timeSince(ts: string): string {
  const d = new Date(ts)
  if (isNaN(d.getTime())) return "—"
  const ms = Date.now() - d.getTime()
  const min = Math.floor(ms / 60_000)
  if (min < 1) return "now"
  if (min < 60) return `${min}m`
  const h = Math.floor(min / 60)
  if (h < 24) return `${h}h`
  return `${Math.floor(h / 24)}d`
}

// Fractional-assumption sizing: every trade buys a flat $10 of the name
// (POSITION_USD / entry shares). Dollar P&L per position = $10 × its return.
const POSITION_USD = 10

function positionPnlUsd(p: LivePick): number | null {
  if (p.current == null || p.plan_entry == null || p.plan_entry <= 0) return null
  const perShare = p.direction === "LONG" ? (p.current - p.plan_entry) : (p.plan_entry - p.current)
  return (POSITION_USD / p.plan_entry) * perShare
}

export function LivePositions({ rows, market, loading }: { rows: LivePick[]; market: string; loading: boolean }) {
  const up = rows.filter(p => (p.pnl_pct ?? 0) > 0).length
  const down = rows.filter(p => (p.pnl_pct ?? 0) < 0).length
  const total = up + down
  const winRate = total > 0 ? Math.round((up / total) * 100) : null
  // Dollar P&L assumes a FRACTIONAL $10 per trade: each position holds $10 of the
  // name, so its $ P&L = $10 × the return % (POSITION_USD / entry shares × move).
  // Same math as the P&L cell, so the card total always matches the column.
  const pnlUsd = rows.reduce((s, p) => s + (positionPnlUsd(p) ?? 0), 0)
  const pnlCount = rows.filter(p => positionPnlUsd(p) != null).length

  if (loading) return (
    <div className="space-y-2">
      <Skeleton className="h-8 w-48" />
      {[1,2,3].map(i => <Skeleton key={i} className="h-10 w-full" />)}
    </div>
  )

  return (
    <div className="space-y-3">
      <div className="grid grid-cols-3 gap-2">
        <Card><CardContent className="flex flex-col items-center gap-1 py-3">
          <Activity className="h-4 w-4 text-muted-foreground" />
          <div className="text-[10px] font-medium uppercase tracking-widest text-muted-foreground">Positions</div>
          <div className="font-mono text-lg font-bold tabular-nums">{rows.length}</div>
        </CardContent></Card>
        <Card><CardContent className="flex flex-col items-center gap-1 py-3">
          <TrendingUp className={`h-4 w-4 ${(winRate ?? 50) >= 50 ? "text-emerald-500" : "text-red-500"}`} />
          <div className="text-[10px] font-medium uppercase tracking-widest text-muted-foreground">Win Rate</div>
          <div className={`font-mono text-lg font-bold tabular-nums ${(winRate ?? 50) >= 50 ? "text-emerald-500" : "text-red-500"}`}>
            {winRate != null ? `${winRate}%` : "—"}
          </div>
          <div className="text-[10px] text-muted-foreground">{up}/{down} open</div>
        </CardContent></Card>
        <Card><CardContent className="flex flex-col items-center gap-1 py-3">
          <TrendingUp className={`h-4 w-4 ${pnlUsd >= 0 ? "text-emerald-500" : "text-red-500"}`} />
          <div className="text-[10px] font-medium uppercase tracking-widest text-muted-foreground">Live P&amp;L</div>
          <div className={`font-mono text-lg font-bold tabular-nums ${pnlUsd >= 0 ? "text-emerald-500" : "text-red-500"}`}>{pnlUsd >= 0 ? "+" : ""}${pnlUsd.toFixed(2)}</div>
          <div className="text-[10px] text-muted-foreground">$10/trade · {pnlCount} open</div>
        </CardContent></Card>
      </div>
      <Separator />
      <div className="flex items-center gap-2 text-xs text-muted-foreground">
        <RefreshCw className="h-3 w-3" />
        <span>Live · 15s</span>
        <Separator orientation="vertical" className="h-3" />
        <Badge variant={market === "OPEN" ? "default" : "secondary"} className={market === "OPEN" ? "bg-emerald-500/15 text-emerald-500" : ""}>{market}</Badge>
      </div>
      {rows.length === 0 ? (
        <Card className="border-dashed"><CardContent className="flex flex-col items-center py-10"><p className="text-sm font-semibold">No open positions</p><p className="mt-1 text-xs text-muted-foreground">Waiting for next auto-run.</p></CardContent></Card>
      ) : (
        <Table>
          <TableHeader><TableRow>
            <TableHead>Symbol</TableHead><TableHead>Dir</TableHead><TableHead className="text-right">Score</TableHead><TableHead className="text-right">Entry</TableHead><TableHead className="text-right">Now</TableHead><TableHead className="text-right">P&L</TableHead>
            <TableHead className="text-right"><Tooltip><TooltipTrigger><span className="inline-flex items-center gap-1"><Target className="h-3 w-3" />Tgt</span></TooltipTrigger><TooltipContent>ATR-based target</TooltipContent></Tooltip></TableHead>
            <TableHead className="text-right"><Tooltip><TooltipTrigger><span className="inline-flex items-center gap-1"><ShieldAlert className="h-3 w-3" />Stop</span></TooltipTrigger><TooltipContent>ATR-based stop</TooltipContent></Tooltip></TableHead>
            <TableHead className="text-right">Age</TableHead>
          </TableRow></TableHeader>
          <TableBody>{rows.map(p => {
            const up = dirUp(p.direction)
            return (<TableRow key={p.id}>
              <TableCell className="font-bold">{p.symbol}</TableCell>
              <TableCell><Badge variant={up ? "default" : "destructive"} className="font-medium">{dirWord(p.direction)}</Badge></TableCell>
              <TableCell className="text-right font-mono tabular-nums text-xs text-muted-foreground">{p.adjusted_score?.toFixed(0) ?? "—"}</TableCell>
              <TableCell className="text-right font-mono tabular-nums">${p.plan_entry.toFixed(2)}</TableCell>
              <TableCell className="text-right font-mono tabular-nums">{p.current != null ? `$${p.current.toFixed(2)}` : "—"}</TableCell>
              <TableCell className="text-right">
                {p.current != null && p.plan_entry != null ? (() => {
                  const pnl = positionPnlUsd(p)
                  return (
                    <div className={`font-mono tabular-nums font-semibold ${(pnl ?? 0) >= 0 ? "text-emerald-500" : "text-red-500"}`}>
                      {pnl != null ? `${pnl >= 0 ? "+" : ""}$${pnl.toFixed(2)}` : "—"}
                    </div>
                  )
                })() : <span className="text-muted-foreground">—</span>}
              </TableCell>
              <TableCell className="text-right font-mono tabular-nums">${p.plan_target.toFixed(2)}</TableCell>
              <TableCell className="text-right font-mono tabular-nums">${p.plan_stop.toFixed(2)}</TableCell>
              <TableCell className="text-right text-xs text-muted-foreground">{timeSince(p.entry_ts)}</TableCell>
            </TableRow>)
          })}</TableBody>
        </Table>
      )}
    </div>
  )
}
