import { useEffect, useState } from "react"
import { ChevronDown } from "lucide-react"
import { api, type PerformanceInfo, type PerfTrade } from "@/lib/api"
import { dirUp, dirWord } from "@/lib/plain"
import { pnlClass, fmtPct } from "@/lib/pnl"
import { Badge, Shimmer, Stat, Table, TableBody, TableCell, TableHead, TableHeader, TableRow, Widget } from "@/components/terminal"

function fmtTs(ts: string | null): string {
  if (!ts) return "—"
  const d = new Date(ts)
  if (isNaN(d.getTime())) return "—"
  return d.toLocaleDateString("en-US", { month: "short", day: "numeric" }) + " " +
    d.toLocaleTimeString("en-US", { hour: "2-digit", minute: "2-digit" })
}

function StatCard({ label, value, sub, tone }: { label: string; value: string; sub?: string; tone?: number | null }) {
  return <Stat label={label} value={value} sub={sub} tone={tone == null || tone === 0 ? undefined : tone > 0 ? "gain" : "loss"} />
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
    <Widget
      title="You vs. the machine"
      subtitle="mean alpha vs SPY per trade — same ledger, same forward grading"
      span={12}
    >
      <div className="grid grid-cols-2">
        {rows.map(([who, st]) => (
          <Stat
            key={who}
            label={who === "HUMAN" ? "You" : "Machine"}
            value={st!.mean_alpha != null ? fmtPct(st!.mean_alpha, 3) : "—"}
            tone={st!.mean_alpha == null ? undefined : st!.mean_alpha >= 0 ? "gain" : "loss"}
            sub={`${st!.n} trades · total ${fmtPct(st!.pnl)} · win ${st!.win_rate?.toFixed(0) ?? "—"}%`}
          />
        ))}
      </div>
      {rows.length < 2 && (
        <div className="border-t border-grid-line px-2 py-1 text-[10px] text-muted-foreground">
          Only one arm has closed trades so far — the comparison needs both.
        </div>
      )}
    </Widget>
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
    <div className="collage">
      <Widget span={12} bodyClassName="grid grid-cols-4">{[1, 2, 3, 4].map(i => <Shimmer key={i} className="m-2 h-9" />)}</Widget>
      <Widget span={12}><Shimmer className="m-2 h-40" /></Widget>
    </div>
  )

  return (
    <div className="collage">
      <Widget
        span={12}
        title="Performance"
        subtitle={`last ${data.days}d · normalized $10/trade · every position exits at its session close`}
        bodyClassName="grid grid-cols-4"
      >
        <StatCard label="Total P&L" value={fmtPct(data.total_return)} sub={`${data.n} exits`} tone={data.total_return} />
        <StatCard label="Win Rate" value={data.win_rate != null ? `${data.win_rate.toFixed(0)}%` : "—"} tone={data.win_rate != null ? (data.win_rate >= 50 ? 1 : -1) : null} />
        <StatCard label="Max Drawdown" value={`−${data.max_drawdown.toFixed(2)}%`} sub="peak-to-trough" tone={data.max_drawdown > 0 ? -1 : null} />
        <StatCard label="Sharpe" value={data.trade_sharpe != null ? data.trade_sharpe.toFixed(2) : "—"} sub={data.daily_sharpe != null ? `daily ${data.daily_sharpe.toFixed(2)}` : "per trade"} tone={data.trade_sharpe} />
      </Widget>

      {/* The actual open question this whole rebuild exists to answer — does a
          human's judgment beat the machine's — goes first, not buried below
          the equity curve as one more tile. */}
      <DeciderSplit by={data.by_decider} />

      <Widget span={7} title="Equity curve" bodyClassName="p-2">
        <EquityChart curve={data.curve} />
      </Widget>

      <Widget span={5} title="By session" bodyClassName="grid grid-cols-3">
        {Object.entries(data.per_market).map(([s, pm]) => (
          <Stat
            key={s}
            label={s === "PRE" ? "Pre" : s === "OPEN" ? "Open" : s === "AFTER" ? "After" : s}
            value={fmtPct(pm.pnl)}
            tone={pm.pnl >= 0 ? "gain" : "loss"}
            sub={`${pm.n} trades · ${pm.wins} wins`}
          />
        ))}
      </Widget>

      <Widget span={12} title="All exits" subtitle="click a row for the drill-down">
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
      </Widget>
    </div>
  )
}
