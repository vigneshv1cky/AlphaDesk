import { useEffect, useState } from "react"
import { Link } from "react-router-dom"
import { api, type ScreenerRow } from "@/lib/api"
import { Card, CardContent } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"

/** "Where should I be looking?" — the front door. A code-computed ranking
 * (earnings proximity + news volume/recency) decides which symbols are worth
 * a look; DeepSeek only narrates WHY for the top of that list. If the AI call
 * ever fails, the row still renders with its raw headlines — a research aid
 * degrading to "here's the news" is fine, an empty page is not. */
export default function ScreenerPage() {
  const [rows, setRows] = useState<ScreenerRow[] | null>(null)
  const [err, setErr] = useState<string | null>(null)

  useEffect(() => {
    let alive = true
    const load = () => api.screener()
      .then(d => { if (alive) setRows(d.symbols) })
      .catch(e => { if (alive) setErr(String(e.message ?? e)) })
    load()
    const t = setInterval(load, 60_000)
    return () => { alive = false; clearInterval(t) }
  }, [])

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-lg font-semibold">Screener</h1>
        <p className="text-sm text-muted-foreground">
          Ranked by earnings proximity and news volume — a written digest for the
          top names, everyone else shows raw headlines. You decide what to trade.
        </p>
      </div>

      {err && <p className="text-sm text-red-600">{err}</p>}
      {rows === null && !err && <p className="text-sm text-muted-foreground">Loading…</p>}
      {rows !== null && rows.length === 0 && (
        <p className="text-sm text-muted-foreground">
          Nothing qualified yet — no upcoming earnings and no fresh news in the window.
        </p>
      )}

      <div className="space-y-3">
        {rows?.map(r => <ScreenerCard key={r.symbol} row={r} />)}
      </div>
    </div>
  )
}

function daysUntil(dateStr: string | null): string | null {
  if (!dateStr) return null
  const d = Math.round((new Date(dateStr + "T00:00:00").getTime() - Date.now()) / 86_400_000)
  if (d < 0) return null
  if (d === 0) return "reports today"
  if (d === 1) return "reports tomorrow"
  return `reports in ${d}d`
}

function ScreenerCard({ row }: { row: ScreenerRow }) {
  const due = daysUntil(row.report_date)
  return (
    <Card>
      <CardContent className="py-4">
        <div className="flex flex-wrap items-start justify-between gap-2">
          <div className="flex items-center gap-2">
            <span className="font-mono text-base font-bold">{row.symbol}</span>
            {due && <Badge variant="secondary">{due}</Badge>}
            {row.article_count > 0 && (
              <span className="text-xs text-muted-foreground">
                {row.article_count} article{row.article_count === 1 ? "" : "s"}
              </span>
            )}
          </div>
          <div className="flex gap-1.5">
            <Link to={`/filings?symbol=${encodeURIComponent(row.symbol)}`}>
              <Button size="sm" variant="ghost">Filings</Button>
            </Link>
            <Link to={`/trade?symbol=${encodeURIComponent(row.symbol)}`}>
              <Button size="sm" variant="outline">Trade →</Button>
            </Link>
          </div>
        </div>

        {row.digest ? (
          <div className="mt-2">
            <p className="text-sm">{row.digest}</p>
            {row.citations.length > 0 && (
              <div className="mt-1.5 flex flex-wrap gap-x-3 gap-y-1">
                {row.citations.map((c, i) => (
                  <a key={i} href={c.url} target="_blank" rel="noreferrer"
                    className="text-[11px] text-muted-foreground underline decoration-dotted hover:text-foreground"
                    title={c.claim}>
                    [{i + 1}] {c.source}
                  </a>
                ))}
              </div>
            )}
          </div>
        ) : row.headlines.length > 0 ? (
          <div className="mt-2 space-y-1">
            <p className="text-[11px] text-muted-foreground">No AI digest yet — raw headlines:</p>
            {row.headlines.slice(0, 3).map((h, i) => (
              <a key={i} href={h.url} target="_blank" rel="noreferrer"
                className="block text-sm text-muted-foreground hover:text-foreground hover:underline">
                {h.title}
                <span className="ml-1.5 text-[11px] text-muted-foreground/70">— {h.source}</span>
              </a>
            ))}
          </div>
        ) : (
          <p className="mt-2 text-sm text-muted-foreground">
            No recent news — flagged purely on upcoming earnings.
          </p>
        )}
      </CardContent>
    </Card>
  )
}
