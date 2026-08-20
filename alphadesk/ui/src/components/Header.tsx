import { useSystem } from "@/lib/queries"

/** Header readout — is data still arriving, and what has the AI cost today.
 *
 * The old counters here (open positions, graded, win rate, total picks) were
 * measurement and went with the execution layer. The market-session cell went
 * later: this terminal reads crypto and futures too, so "CLOSED" was only ever
 * true of US equities and read as though the whole board had stopped. */
function Cell({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex shrink-0 items-baseline gap-1 border-l border-border px-2 first:border-l-0">
      <span className="text-[11px] uppercase tracking-[0.06em] text-muted-foreground">{label}</span>
      <span className="num text-[14px] font-semibold">{value}</span>
    </div>
  )
}

export function Header() {
  const { data } = useSystem()
  const news = data?.news
  return (
    <div className="flex items-center">
      <Cell label="news" value={news ? String(news.articles_today) : "—"} />
      <Cell label="ai calls" value={news ? String(news.calls_today) : "—"} />
      <Cell label="up" value={data ? `${Math.floor(data.uptime_s / 3600)}h` : "—"} />
    </div>
  )
}
