import { useSystem } from "@/lib/queries"

/** Header readout. The old counters here (open positions, graded, win rate,
 * total picks) were all measurement — they went with the execution layer.
 * What's left is what a consumption terminal actually wants pinned: is the
 * market open, and is data still arriving. */
function Cell({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex shrink-0 items-baseline gap-1 border-l border-border px-2 first:border-l-0">
      <span className="text-[9px] uppercase tracking-[0.06em] text-muted-foreground">{label}</span>
      <span className="num text-[11px] font-semibold">{value}</span>
    </div>
  )
}

export function Header() {
  const { data } = useSystem()
  const news = data?.news
  return (
    <div className="flex items-center">
      <Cell label="market" value={data?.market ?? "—"} />
      <Cell label="news" value={news ? String(news.articles_today) : "—"} />
      <Cell label="ai calls" value={news ? String(news.calls_today) : "—"} />
      <Cell label="up" value={data ? `${Math.floor(data.uptime_s / 3600)}h` : "—"} />
    </div>
  )
}
