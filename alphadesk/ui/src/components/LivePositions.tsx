import { useState } from "react"
import { RefreshCw, Target, ShieldAlert } from "lucide-react"
import { dirUp, dirWord } from "@/lib/plain"
import type { LivePick } from "@/lib/api"
import { pnlClass, fmtUsd } from "@/lib/pnl"
import { Badge, Empty, Shimmer, Stat, Table, TableBody, TableCell, TableHead, TableHeader, TableRow, Tag } from "@/components/terminal"
import { InfoTip } from "@/components/InfoTip"

const SESSIONS = [
  { value: "ALL", label: "All" },
  { value: "PRE", label: "Pre-Market" },
  { value: "OPEN", label: "Open Market" },
  { value: "AFTER", label: "After Hours" },
] as const

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
  const [session, setSession] = useState<(typeof SESSIONS)[number]["value"]>("ALL")
  const filtered = session === "ALL" ? rows : rows.filter(p => p.session === session)

  const up = filtered.filter(p => (p.pnl_pct ?? 0) > 0).length
  const down = filtered.filter(p => (p.pnl_pct ?? 0) < 0).length
  const total = up + down
  const winRate = total > 0 ? Math.round((up / total) * 100) : null
  // Dollar P&L is a NORMALIZED convention, not real position sizing: every
  // trade is treated as $10 of notional purely so trades of different prices
  // compare on the same $ scale here. It does not reflect actual order size —
  // manual bookings fill at whatever price the market gives, whole-share.
  const pnlUsd = filtered.reduce((s, p) => s + (positionPnlUsd(p) ?? 0), 0)
  const pnlCount = filtered.filter(p => positionPnlUsd(p) != null).length

  if (loading) return (
    <div className="space-y-2">
      <Shimmer className="h-8 w-48" />
      {[1,2,3].map(i => <Shimmer key={i} className="h-[24px] w-full" />)}
    </div>
  )

  return (
    <div>
      <div className="flex flex-wrap items-stretch border-b border-border">
        {SESSIONS.map(s => (
          <button
            key={s.value}
            onClick={() => setSession(s.value)}
            className={`border-b-2 px-2 py-1 text-[10px] font-medium uppercase tracking-[0.06em] transition-colors ${
              session === s.value
                ? "border-accent text-foreground"
                : "border-transparent text-muted-foreground hover:bg-muted hover:text-foreground"
            }`}
          >
            {s.label}
          </button>
        ))}
      </div>
      <div className="grid grid-cols-3 border-b border-border">
        <Stat label="Positions" value={filtered.length} />
        <Stat
          label="Win rate"
          value={winRate != null ? `${winRate}%` : "—"}
          tone={(winRate ?? 50) >= 50 ? "gain" : "loss"}
          sub={`${up}/${down} open`}
        />
        <Stat
          label="Live P&L"
          value={fmtUsd(pnlUsd)}
          tone={pnlUsd >= 0 ? "gain" : "loss"}
          sub={`normalized $10/trade · ${pnlCount} open`}
        />
      </div>
      <div className="flex items-center gap-1.5 border-b border-border px-2 py-1 text-[10px] text-muted-foreground">
        <RefreshCw className="h-3 w-3" />
        <span>live · 15s</span>
        <Tag tone={market === "OPEN" ? "gain" : "neutral"}>{market}</Tag>
      </div>
      {filtered.length === 0 ? (
        <Empty>
          No open positions{session !== "ALL" ? ` in ${session}` : ""} — nothing books itself.{" "}
          <a href="/screener" className="underline hover:text-foreground">Screener</a> to find something worth trading.
        </Empty>
      ) : (
        <Table>
          <TableHeader><TableRow>
            <TableHead>Symbol</TableHead><TableHead>Dir</TableHead><TableHead className="text-right">Score</TableHead><TableHead className="text-right">Entry</TableHead><TableHead className="text-right">Now</TableHead><TableHead className="text-right">P&L</TableHead>
            <TableHead className="text-right"><InfoTip tip="ATR-based target"><span className="inline-flex items-center gap-1"><Target className="h-3 w-3" />Tgt</span></InfoTip></TableHead>
            <TableHead className="text-right"><InfoTip tip="ATR-based stop"><span className="inline-flex items-center gap-1"><ShieldAlert className="h-3 w-3" />Stop</span></InfoTip></TableHead>
            <TableHead className="text-right">Age</TableHead>
          </TableRow></TableHeader>
          <TableBody>{filtered.map(p => {
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
                    <div className={`font-mono tabular-nums font-semibold ${pnlClass(pnl)}`}>
                      {pnl != null ? fmtUsd(pnl) : "—"}
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
