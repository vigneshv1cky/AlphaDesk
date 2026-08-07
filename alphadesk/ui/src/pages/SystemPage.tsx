import { useEffect, useState } from "react"
import { Card, CardContent } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { Skeleton } from "@/components/ui/skeleton"
import { api, type SystemInfo } from "@/lib/api"

function fmtAgo(ts: string | null): string {
  if (!ts) return "—"
  const d = new Date(ts)
  if (isNaN(d.getTime())) return "—"
  const min = Math.floor((Date.now() - d.getTime()) / 60_000)
  if (min < 1) return "now"
  if (min < 60) return `${min}m ago`
  if (min < 60 * 24) return `${Math.floor(min / 60)}h ago`
  return `${Math.floor(min / (60 * 24))}d ago`
}

function StatCard({ label, value, sub, tone }: { label: string; value: string; sub?: string; tone?: number | null }) {
  const color = tone == null ? "" : tone > 0 ? "text-emerald-500" : tone < 0 ? "text-red-500" : ""
  return (
    <Card><CardContent className="flex flex-col items-center gap-1 py-3">
      <div className="text-[10px] font-medium uppercase tracking-widest text-muted-foreground">{label}</div>
      <div className={`font-mono text-lg font-bold tabular-nums ${color}`}>{value}</div>
      {sub && <div className="text-[10px] text-muted-foreground">{sub}</div>}
    </CardContent></Card>
  )
}

export default function SystemPage() {
  const [info, setInfo] = useState<SystemInfo | null>(null)
  useEffect(() => {
    let alive = true
    const load = () => api.system().then(d => { if (alive) setInfo(d) }).catch(() => {})
    load()
    const t = setInterval(load, 30_000)
    return () => { alive = false; clearInterval(t) }
  }, [])

  if (!info) return (
    <div className="space-y-3">
      <Skeleton className="h-8 w-48" />
      <div className="grid grid-cols-4 gap-2">{[1, 2, 3, 4].map(i => <Skeleton key={i} className="h-24 w-full rounded-xl" />)}</div>
    </div>
  )

  const runs = info.runs_today
  const success = runs.total > 0 ? Math.round((runs.with_picks / runs.total) * 100) : null
  const funnel = info.funnel_today
  const uptimeH = Math.floor(info.uptime_s / 3600)
  const uptimeM = Math.floor((info.uptime_s % 3600) / 60)
  const skippedRate = funnel.candidates > 0 ? Math.round((funnel.skipped / funnel.candidates) * 100) : null

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-lg font-bold tracking-tight">System Health</h1>
        <p className="text-xs text-muted-foreground">
          Is the desk alive and covering? Runs, coverage funnel, risk-rail state.
        </p>
      </div>

      <div className="grid grid-cols-4 gap-2">
        <StatCard label="Last Run" value={fmtAgo(runs.last_ts)} sub={runs.last_ts ? "Find Trades" : "never"} />
        <StatCard label="Runs Today" value={String(runs.total)} sub={success != null ? `${success}% booked picks` : "no runs yet"} tone={success != null ? (success >= 50 ? 1 : -1) : null} />
        <StatCard label="Open" value={String(info.open_positions)} sub="positions now" tone={info.open_positions > 0 ? 1 : null} />
        <StatCard label="Uptime" value={`${uptimeH}h ${uptimeM}m`} sub="since last deploy" />
      </div>

      <div className="grid grid-cols-4 gap-2">
        <StatCard label="Candidates" value={String(funnel.candidates)} sub="scored today" />
        <StatCard label="Picked" value={String(funnel.picked)} sub="booked today" tone={funnel.picked > 0 ? 1 : null} />
        <StatCard label="Dropped" value={String(funnel.skipped)} sub={skippedRate != null ? `${skippedRate}% of candidates` : ""} tone={skippedRate != null && skippedRate > 80 ? -1 : null} />
        <StatCard label="Graded" value={String(info.graded)} sub={`${info.exited} exited`} />
      </div>

      <Card><CardContent className="space-y-2 py-4 text-xs text-muted-foreground">
        <div className="flex items-center gap-2">
          <span className="font-medium text-foreground">Risk rails</span>
          <Badge variant="secondary">max 20 open</Badge>
          <Badge variant="secondary">2/sector·dir</Badge>
          <Badge variant="secondary">−10% daily stop</Badge>
        </div>
        <p>
          Circuit breakers gate new entries when enforced: at max open positions, concentration per
          sector+direction, or a daily realized loss past the stop. Each trigger is logged in the funnel
          with a reason and (if configured) sends an alert.
        </p>
        <p className="text-[11px]">
          Market: <span className="font-semibold text-foreground">{info.market}</span>. Sessions are
          self-contained — every position exits at its session close.
        </p>
      </CardContent></Card>
    </div>
  )
}
