import { useEffect, useState } from "react"
import { Card, CardContent } from "@/components/ui/card"
import { Skeleton } from "@/components/ui/skeleton"
import { api, type SystemInfo } from "@/lib/api"
import { pnlClass } from "@/lib/pnl"

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
  const color = pnlClass(tone)
  return (
    <Card><CardContent className="flex flex-col items-center gap-1 py-3">
      <div className="text-[10px] font-medium uppercase tracking-widest text-muted-foreground">{label}</div>
      <div className={`font-mono text-lg font-bold tabular-nums ${color}`}>{value}</div>
      {sub && <div className="text-[10px] text-muted-foreground">{sub}</div>}
    </CardContent></Card>
  )
}

/** Is the terminal alive, and is the one thing that still runs unattended —
 * the news/AI pipeline — actually working? Deliberately does NOT show
 * "runs today" / "candidates scored" / risk rails: those described the
 * autonomous entry engine, deleted 2026-08-16, and would now be permanently
 * frozen numbers pretending to be live. */
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

  const uptimeH = Math.floor(info.uptime_s / 3600)
  const uptimeM = Math.floor((info.uptime_s % 3600) / 60)
  const news = info.news
  const newsStale = !news.last_article_at
    || (Date.now() - new Date(news.last_article_at).getTime()) > 2 * 3600_000

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-lg font-bold tracking-tight">System Health</h1>
        <p className="text-xs text-muted-foreground">
          Nothing here trades. This checks the terminal's own pulse: is the operator's
          book being watched, and is the news pipeline still feeding the Screener.
        </p>
      </div>

      <div className="grid grid-cols-4 gap-2">
        <StatCard label="Open" value={String(info.open_positions)} sub="positions now" tone={info.open_positions > 0 ? 1 : null} />
        <StatCard label="Graded" value={String(info.graded)} sub={`${info.exited} exited`} />
        <StatCard label="Uptime" value={`${uptimeH}h ${uptimeM}m`} sub="since last deploy" />
        <StatCard label="Market" value={info.market} sub="current session" />
      </div>

      <Card>
        <CardContent className="space-y-3 py-4">
          <div className="flex items-center justify-between">
            <span className="text-xs font-bold uppercase tracking-wider text-muted-foreground">
              News → Screener pipeline
            </span>
            <span className={`text-[11px] font-medium ${newsStale ? "text-amber-600 dark:text-amber-400" : "text-emerald-500"}`}>
              {newsStale ? "stale" : "healthy"}
            </span>
          </div>
          <div className="grid grid-cols-4 gap-2">
            <StatCard label="Last Article" value={fmtAgo(news.last_article_at)} />
            <StatCard label="Articles Today" value={String(news.articles_today)} />
            <StatCard label="AI Calls Today" value={String(news.calls_today)} />
            <StatCard label="Tokens Today" value={`${((news.tokens_today_in + news.tokens_today_out) / 1000).toFixed(1)}k`} />
          </div>
          <p className="text-[11px] text-muted-foreground">
            Polls Polygon for ticker news, summarizes with DeepSeek, caches the digest per
            symbol. A DeepSeek outage degrades the Screener to raw headlines with real source
            links — never an empty page. See <code className="text-foreground">/api/tokens</code> for
            the full spend breakdown.
          </p>
        </CardContent>
      </Card>

      <Card>
        <CardContent className="space-y-1.5 py-4 text-[11px] text-muted-foreground">
          <p>
            Trades enter this system exactly one way: a human clicking Book on{" "}
            <code className="text-foreground">/trade</code>. There is no autonomous entry path,
            no position cap, no daily-loss circuit breaker enforced against a manual decision —
            those existed for the unattended bot and were removed with it (2026-08-16).
          </p>
          <p>
            Sessions are self-contained — every position exits at its session close, never
            carrying into another market.
          </p>
        </CardContent>
      </Card>
    </div>
  )
}
