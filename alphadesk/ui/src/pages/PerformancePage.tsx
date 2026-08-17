import { useEffect, useState } from "react"
import { Card, CardContent } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { Skeleton } from "@/components/ui/skeleton"
import { Separator } from "@/components/ui/separator"
import { Table, TableHeader, TableBody, TableRow, TableHead, TableCell } from "@/components/ui/table"
import { ChevronDown } from "lucide-react"
import { api, type PerformanceInfo, type PerfTrade } from "@/lib/api"
import { dirUp, dirWord } from "@/lib/plain"
import { pnlClass, fmtPct } from "@/lib/pnl"

function fmtTs(ts: string | null): string {
  if (!ts) return "—"
  const d = new Date(ts)
  if (isNaN(d.getTime())) return "—"
  return d.toLocaleDateString("en-US", { month: "short", day: "numeric" }) + " " +
    d.toLocaleTimeString("en-US", { hour: "2-digit", minute: "2-digit" })
}

function StatCard({ label, value, sub, tone }: { label: string; value: string; sub?: string; tone?: number | null }) {
  const color = pnlClass(tone)
  return (
    <Card><CardContent className="flex flex-col items-center gap-1 py-3">
      <div className="text-[10px] font-medium uppercase tracking-widest text-muted-foreground">{label}</div>
      <div className={`font-mono text-lg font-bold tabular-nums ${color}`}>{value}</div>
      {sub && <div className="text-[10px] text-muted-foreground">{sub}</div>}
    </CardContent></Card>
  )
}

// Simple SVG equity curve — cumulative realized return (equal-weight book) and
// cumulative alpha, one point per exit.
function EquityChart({ curve }: { curve: PerformanceInfo["curve"] }) {
  if (curve.length < 2) return (
    <div className="py-8 text-center text-xs text-muted-foreground">Not enough exits to plot yet.</div>
  )
  const W = 560, H = 180, PAD = 24
  const vals = curve.map(p => p.cum)
  const alphas = curve.map(p => p.alpha)
  const all = [...vals, ...alphas]
  const lo = Math.min(...all), hi = Math.max(...all)
  const span = (hi - lo) || 1
  const x = (i: number) => PAD + (i / (curve.length - 1)) * (W - 2 * PAD)
  const y = (v: number) => H - PAD - ((v - lo) / span) * (H - 2 * PAD)
  const line = (key: "cum" | "alpha") => curve.map((p, i) => `${x(i)},${y(p[key])}`).join(" ")
  const last = curve[curve.length - 1]
  // Unlike the rest of the app's gain/loss coloring, this line previously
  // never flipped red on a losing stretch — it was hardcoded emerald
  // regardless of sign. Fixed here by conditioning on the final cumulative
  // value, same as every other P&L display.
  const posStroke = last.cum >= 0 ? "stroke-gain" : "stroke-loss"
  const posFill = last.cum >= 0 ? "fill-gain" : "fill-loss"
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="w-full" role="img" aria-label="equity curve">
      <line x1={PAD} y1={y(0)} x2={W - PAD} y2={y(0)} stroke="currentColor" strokeOpacity={0.15} strokeDasharray="3 3" />
      <polyline points={line("alpha")} fill="none" stroke="#6366f1" strokeWidth={1.5} strokeOpacity={0.5} />
      <polyline points={line("cum")} fill="none" className={posStroke} strokeWidth={2} />
      <circle cx={x(curve.length - 1)} cy={y(last.cum)} r={3} className={posFill} />
      <text x={PAD} y={12} fill="currentColor" fontSize={9} opacity={0.6}>cumulative return % (equal-weight)</text>
      <text x={W - PAD} y={12} fontSize={9} textAnchor="end" className={posFill}>P&L</text>
      <text x={W - PAD} y={22} fill="#6366f1" fontSize={9} textAnchor="end">alpha</text>
    </svg>
  )
}

// Two <TableRow>s per trade, not one <tr> with a nested toggle button —
// <button> isn't valid inside <tr> (only <td>/<th> are), so the summary row
// toggles via onClick on the <tr> itself, and the detail panel is a second,
// conditionally-rendered row with one full-width <TableCell colSpan>.
function TradeRow({ t }: { t: PerfTrade }) {
  const [open, setOpen] = useState(false)
  const ret = t.exit_return_pct
  const alpha = t.exit_alpha ?? t.alpha_net
  const up = dirUp(t.direction)
  return (
    <>
      <TableRow
        onClick={() => setOpen(v => !v)}
        aria-expanded={open}
        className="cursor-pointer"
      >
        <TableCell className="font-semibold">{t.symbol}</TableCell>
        <TableCell><Badge variant={up ? "default" : "destructive"} className="font-medium">{dirWord(t.direction)}</Badge></TableCell>
        <TableCell className="text-xs text-muted-foreground">{t.session}</TableCell>
        <TableCell className="text-xs text-muted-foreground">{fmtTs(t.exit_ts)}</TableCell>
        <TableCell className={`text-right font-mono tabular-nums font-semibold ${pnlClass(ret)}`}>
          {ret != null ? fmtPct(ret) : "—"}
        </TableCell>
        <TableCell className={`text-right font-mono tabular-nums ${pnlClass(alpha)}`}>
          {alpha != null ? fmtPct(alpha) : "—"}
        </TableCell>
        <TableCell>
          <ChevronDown className={`h-3.5 w-3.5 shrink-0 text-muted-foreground/40 transition-transform ${open ? "" : "-rotate-90"}`} />
        </TableCell>
      </TableRow>
      {open && (
        <TableRow className="hover:bg-transparent">
          <TableCell colSpan={7} className="bg-muted/40">
            <div className="space-y-1.5 py-1 text-xs leading-relaxed text-muted-foreground">
              {t.thesis && <p className="text-foreground/80">{t.thesis}</p>}
              <div className="flex flex-wrap gap-x-4 gap-y-1">
                <span>entry <b className="text-foreground">${t.entry_price ?? t.plan_entry ?? "—"}</b></span>
                <span>target <b className="text-gain">${t.plan_target ?? "—"}</b></span>
                <span>stop <b className="text-loss">${t.plan_stop ?? "—"}</b></span>
                <span>MFE <b className="text-foreground">{t.mfe_pct != null ? `${t.mfe_pct.toFixed(1)}%` : "—"}</b></span>
                <span>MAE <b className="text-foreground">{t.mae_pct != null ? `${t.mae_pct.toFixed(1)}%` : "—"}</b></span>
                <span>score <b className="text-foreground">{t.score != null ? t.score.toFixed(1) : "—"}</b></span>
              </div>
              {t.debate?.quant_signals && (
                <div className="flex flex-wrap gap-1.5 pt-1">
                  {Object.entries(t.debate.quant_signals).map(([k, v]) => (
                    <Badge key={k} variant="secondary" className={`text-[10px] tabular-nums ${pnlClass(v)}`}>
                      {k} {Math.round(v)}
                    </Badge>
                  ))}
                </div>
              )}
            </div>
          </TableCell>
        </TableRow>
      )}
    </>
  )
}

/** You vs the machine, scored identically.
 *
 * The bot keeps booking on paper as a control arm, so this answers the one
 * question a P&L alone never can: is discretion adding anything? Both arms are
 * graded forward against SPY by the same code, so the comparison is fair. */
function DeciderSplit({ by }: { by: PerformanceInfo["by_decider"] }) {
  const rows = (["HUMAN", "MACHINE"] as const).map(k => [k, by?.[k]] as const).filter(([, v]) => v)
  if (!rows.length) return null
  return (
    <Card className="border-indigo-500/30"><CardContent className="py-4">
      <div className="mb-0.5 text-sm font-bold tracking-tight">You vs. the machine</div>
      <p className="mb-3 text-[11px] text-muted-foreground">
        Same ledger, same forward grading vs SPY. The autonomous engine keeps booking on
        paper as a control arm — this is the answer to whether judgment beats it.
      </p>
      <div className="grid gap-2 sm:grid-cols-2">
        {rows.map(([who, s]) => (
          <div key={who} className="rounded-lg border p-3">
            <div className="flex items-baseline justify-between">
              <span className="text-sm font-semibold">{who === "HUMAN" ? "You" : "Machine"}</span>
              <span className="text-[11px] text-muted-foreground">{s!.n} trades</span>
            </div>
            <div className={`mt-1 font-mono text-xl font-bold tabular-nums ${pnlClass(s!.mean_alpha)}`}>
              {s!.mean_alpha != null ? fmtPct(s!.mean_alpha, 3) : "—"}
            </div>
            <div className="text-[10px] text-muted-foreground">mean alpha vs SPY per trade</div>
            <div className="mt-2 font-mono text-[11px] text-muted-foreground tabular-nums">
              total {fmtPct(s!.pnl)} · win {s!.win_rate?.toFixed(0) ?? "—"}%
            </div>
          </div>
        ))}
      </div>
      {rows.length < 2 && (
        <p className="mt-2 text-[11px] text-muted-foreground">
          Only one arm has closed trades so far — the comparison needs both.
        </p>
      )}
    </CardContent></Card>
  )
}

export default function PerformancePage() {
  const [data, setData] = useState<PerformanceInfo | null>(null)
  useEffect(() => {
    let alive = true
    const load = () => api.performance().then(d => { if (alive) setData(d) }).catch(() => {})
    load()
    const t = setInterval(load, 60_000)
    return () => { alive = false; clearInterval(t) }
  }, [])

  if (!data) return (
    <div className="space-y-3">
      <Skeleton className="h-8 w-56" />
      <div className="grid grid-cols-4 gap-2">{[1, 2, 3, 4].map(i => <Skeleton key={i} className="h-24 w-full rounded-xl" />)}</div>
      <Skeleton className="h-48 w-full rounded-xl" />
    </div>
  )

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-lg font-bold tracking-tight">Performance</h1>
        <p className="text-xs text-muted-foreground">
          Realized results over the last {data.days} days — normalized $10/trade for comparison,
          every position exits at its session close.
        </p>
      </div>

      <div className="grid grid-cols-4 gap-2">
        <StatCard label="Total P&L" value={fmtPct(data.total_return)} sub={`${data.n} exits`} tone={data.total_return} />
        <StatCard label="Win Rate" value={data.win_rate != null ? `${data.win_rate.toFixed(0)}%` : "—"} tone={data.win_rate != null ? (data.win_rate >= 50 ? 1 : -1) : null} />
        <StatCard label="Max Drawdown" value={`−${data.max_drawdown.toFixed(2)}%`} sub="peak-to-trough" tone={data.max_drawdown > 0 ? -1 : null} />
        <StatCard label="Sharpe" value={data.trade_sharpe != null ? data.trade_sharpe.toFixed(2) : "—"} sub={data.daily_sharpe != null ? `daily ${data.daily_sharpe.toFixed(2)}` : "per trade"} tone={data.trade_sharpe} />
      </div>

      {/* The actual open question this whole rebuild exists to answer — does a
          human's judgment beat the machine's — goes first, not buried below
          the equity curve as one more tile. */}
      <DeciderSplit by={data.by_decider} />

      <Card><CardContent className="py-4">
        <EquityChart curve={data.curve} />
      </CardContent></Card>

      <div className="grid grid-cols-3 gap-2">
        {Object.entries(data.per_market).map(([s, pm]) => (
          <Card key={s}><CardContent className="flex flex-col items-center gap-1 py-3">
            <div className="text-[10px] font-medium uppercase tracking-widest text-muted-foreground">
              {s === "PRE" ? "Pre-Market" : s === "OPEN" ? "Open Market" : s === "AFTER" ? "After Hours" : s}
            </div>
            <div className={`font-mono text-lg font-bold tabular-nums ${pnlClass(pm.pnl)}`}>
              {fmtPct(pm.pnl)}
            </div>
            <div className="text-[10px] text-muted-foreground">{pm.n} trades · {pm.wins} wins</div>
          </CardContent></Card>
        ))}
      </div>

      <Separator />

      <div>
        <h2 className="mb-2 text-xs font-bold uppercase tracking-wider text-muted-foreground">All exits — click for the drill-down</h2>
        <Table>
          <TableHeader><TableRow>
            <TableHead>Symbol</TableHead>
            <TableHead>Dir</TableHead>
            <TableHead>Sess</TableHead>
            <TableHead>Exited</TableHead>
            <TableHead className="text-right">P&L</TableHead>
            <TableHead className="text-right">Alpha</TableHead>
            <TableHead />
          </TableRow></TableHeader>
          <TableBody>
            {data.trades.map(t => <TradeRow key={String(t.id)} t={t} />)}
          </TableBody>
        </Table>
      </div>
    </div>
  )
}
