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
 * the news/AI pipeline — actually working?
 *
 * Shows no trading counters. It used to claim as much in this comment while
 * rendering "open positions" and "graded" anyway; the API stopped returning
 * those with the execution layer, so the page had been printing the literal
 * string "undefined" ever since. Their slots now carry the selected providers,
 * which is the question this page actually gets asked — "why is there no
 * news" is usually "the feed you configured is not the one you think". */
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
        subtitle="nothing here trades — this is the terminal's own pulse: is data still arriving, and from where"
        bodyClassName="grid grid-cols-4"
      >
        <StatCard label="Uptime" value={`${uptimeH}h ${uptimeM}m`} sub="since last deploy" />
        {/* Kept here and nowhere else: on a diagnostics page this is server state
            (it explains a quiet ingest loop), not a claim about what can be traded. */}
        <StatCard label="US equity session" value={info.market} sub="equities only — crypto and futures run around the clock" />
        <StatCard label="Model" value={info.providers?.selected.llm ?? "—"} sub="LLM_PROVIDER" />
        <StatCard label="News feed" value={info.providers?.selected.news ?? "—"} sub="NEWS_PROVIDER" />
      </Widget>

      <Widget span={12} title="News / AI pipeline">
        <div className="space-y-2 p-2">
          <div className="flex items-center justify-between">
            <span className="text-xs font-bold uppercase tracking-wider text-muted-foreground">
              News → Screener pipeline
            </span>
            <span className={`text-[14px] font-medium ${newsStale ? "text-amber-600 dark:text-amber-400" : "text-emerald-500"}`}>
              {newsStale ? "stale" : "healthy"}
            </span>
          </div>
          <div className="grid grid-cols-4 gap-2">
            <StatCard label="Last Article" value={fmtAgo(news.last_article_at)} />
            <StatCard label="Articles Today" value={String(news.articles_today)} />
            <StatCard label="AI Calls Today" value={String(news.calls_today)} />
            <StatCard label="Tokens Today" value={`${((news.tokens_today_in + news.tokens_today_out) / 1000).toFixed(1)}k`} />
          </div>
          <p className="text-[14px] text-muted-foreground">
            Polls the configured news feed and labels each article with the configured
            model. Nothing is summarized in the background — the AI runs only when you
            ask, so an idle terminal spends nothing, and a model outage leaves the window
            and its real source links intact while failing only the ask. See{" "}
            <code className="text-foreground">/api/tokens</code> for the full spend breakdown.
          </p>
        </div>
      </Widget>

      <Widget span={12} title="What this is">
        <div className="space-y-1.5 p-2 text-[14px] text-muted-foreground">
          <p>
            AlphaDesk is a consumption terminal: it fetches, reads and presents market
            information. It holds no positions, books no trades and keeps no score — the
            execution and measurement layers were removed on 2026-08-18.
          </p>
          <p>
            The AI reads and summarizes only, and every claim it renders is tied to a
            source this server fetched. Anything it cannot back is dropped before you
            see it.
          </p>
        </div>
      </Widget>
    </div>
  )
}
