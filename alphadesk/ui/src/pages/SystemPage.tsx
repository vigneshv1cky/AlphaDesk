import { useSystem } from "@/lib/queries"
import { Shimmer, Stat, Widget } from "@/components/terminal"

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
  return <Stat label={label} value={value} sub={sub} tone={tone == null || tone === 0 ? undefined : tone > 0 ? "gain" : "loss"} />
}

/** Is the terminal alive, and is the one thing that still runs unattended —
 * the news/AI pipeline — actually working? Deliberately does NOT show
 * "runs today" / "candidates scored" / risk rails: those described the
 * autonomous entry engine, deleted 2026-08-16, and would now be permanently
 * frozen numbers pretending to be live. */
export default function SystemPage() {
  const { data: info } = useSystem()

  if (!info) return (
    <div className="collage">
      <Widget span={12} bodyClassName="grid grid-cols-4">
        {[1, 2, 3, 4].map(i => <Shimmer key={i} className="m-2 h-9" />)}
      </Widget>
    </div>
  )

  const uptimeH = Math.floor(info.uptime_s / 3600)
  const uptimeM = Math.floor((info.uptime_s % 3600) / 60)
  const news = info.news
  const newsStale = !news.last_article_at
    || (Date.now() - new Date(news.last_article_at).getTime()) > 2 * 3600_000

  return (
    <div className="collage">
      <Widget
        span={12}
        title="System health"
        subtitle="nothing here trades — this is the terminal's own pulse: is your book watched, is news still feeding the Screener"
        bodyClassName="grid grid-cols-4"
      >
        <StatCard label="Open" value={String(info.open_positions)} sub="positions now" tone={info.open_positions > 0 ? 1 : null} />
        <StatCard label="Graded" value={String(info.graded)} sub={`${info.exited} exited`} />
        <StatCard label="Uptime" value={`${uptimeH}h ${uptimeM}m`} sub="since last deploy" />
        <StatCard label="Market" value={info.market} sub="current session" />
      </Widget>

      <Widget span={12} title="News / AI pipeline">
        <div className="space-y-2 p-2">
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
            Polls Polygon for ticker news and enriches it with DeepSeek. Nothing is
            summarized in the background any more — the Screener asks only when you do, so
            an idle terminal spends nothing. A DeepSeek outage leaves the window and its
            real source links intact and fails only the ask. See{" "}
            <code className="text-foreground">/api/tokens</code> for the full spend breakdown.
          </p>
        </div>
      </Widget>

      <Widget span={12} title="Ground rules">
        <div className="space-y-1.5 p-2 text-[11px] text-muted-foreground">
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
        </div>
      </Widget>
    </div>
  )
}
