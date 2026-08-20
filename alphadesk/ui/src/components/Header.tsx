import { useSystem } from "@/lib/queries"

/** Header readout.
 *
 * Deliberately almost empty. This carried news/ai-calls/uptime counters, which
 * are operator telemetry rather than market information — theirs carries view
 * controls, and Health already reports all three. A market terminal's top bar
 * should be about the market.
 *
 * What survives is a single staleness signal: if the news pipeline has gone
 * quiet the board is showing you an old window, and that IS something a reader
 * needs to know without going to look for it.
 */
export function Header() {
  const { data } = useSystem()
  const last = data?.news.last_article_at
  const stale = data ? !last || (Date.now() - new Date(last).getTime()) > 2 * 3600_000 : false
  if (!stale) return null
  return (
    <div className="flex items-center px-2">
      <span
        className="border border-loss/40 px-1.5 py-[1px] text-[11px] font-medium text-loss"
        title={last ? `last article ${new Date(last).toLocaleString()}` : "no articles ingested"}
      >
        news stale
      </span>
    </div>
  )
}
