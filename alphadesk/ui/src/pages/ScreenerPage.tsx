import { useEffect, useState } from "react"
import { Link } from "react-router-dom"
import { api, type ScreenerAnswer, type ScreenerRow } from "@/lib/api"
import { Card, CardContent } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"

/** The front door — an inventory of what's in the window, and an AI you ask.
 *
 * Nothing here is ranked. The list is every symbol with fresh news or a
 * report inside the horizon, alphabetical: ordering a list is itself a
 * judgment, and the judgment is the operator's. The AI writes nothing on its
 * own either — ask a question and one call reads the WHOLE window (every
 * article and report, across every symbol) and answers it, citing numbered
 * items resolved server-side back to real stored records. */
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

  const withNews = rows?.filter(r => r.article_count > 0).length ?? 0
  const reporting = rows?.filter(r => r.report_date).length ?? 0

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-lg font-semibold">Screener</h1>
        <p className="text-sm text-muted-foreground">
          Everything in the window — symbols with fresh news or a report coming up,
          listed alphabetically. Nothing is ranked or scored. Ask a question below and
          the AI reads all of it at once.
        </p>
      </div>

      <AskBox />

      {err && <p className="text-sm text-red-600">{err}</p>}
      {rows === null && !err && <p className="text-sm text-muted-foreground">Loading…</p>}
      {rows !== null && rows.length === 0 && (
        <p className="text-sm text-muted-foreground">
          Nothing in the window yet — no upcoming earnings and no fresh news.
        </p>
      )}

      {rows !== null && rows.length > 0 && (
        <p className="text-xs text-muted-foreground">
          {rows.length} symbol{rows.length === 1 ? "" : "s"} · {withNews} with news ·{" "}
          {reporting} reporting soon
        </p>
      )}

      <div className="space-y-3">
        {rows?.map(r => <ScreenerCard key={r.symbol} row={r} />)}
      </div>
    </div>
  )
}

function AskBox() {
  const [question, setQuestion] = useState("")
  const [answer, setAnswer] = useState<ScreenerAnswer | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [asking, setAsking] = useState(false)

  const ask = (e: React.FormEvent) => {
    e.preventDefault()
    if (!question.trim()) return
    setAsking(true)
    setErr(null)
    setAnswer(null)
    api.askScreener(question.trim())
      .then(setAnswer)
      .catch(e => setErr(String(e.message ?? e)))
      .finally(() => setAsking(false))
  }

  return (
    <Card>
      <CardContent className="space-y-3 py-4">
        <form onSubmit={ask} className="flex flex-col gap-2 sm:flex-row sm:items-start">
          <textarea
            value={question}
            onChange={e => setQuestion(e.target.value)}
            placeholder="e.g. What's the biggest news in the window? Anything unusual before earnings this week?"
            rows={2}
            className="flex-1 rounded-md border bg-background px-3 py-2 text-sm"
          />
          <Button type="submit" disabled={asking || !question.trim()} className="shrink-0">
            {asking ? "Reading…" : "Ask"}
          </Button>
        </form>

        {err && <p className="text-sm text-red-600">{err}</p>}

        {answer && (
          <div className="space-y-2 border-t pt-3">
            <p className="whitespace-pre-wrap text-sm">{answer.answer}</p>
            <p className="text-[11px] text-muted-foreground">
              Read {answer.considered.articles} article
              {answer.considered.articles === 1 ? "" : "s"} and{" "}
              {answer.considered.earnings} upcoming report
              {answer.considered.earnings === 1 ? "" : "s"} across{" "}
              {answer.considered.symbols} symbol
              {answer.considered.symbols === 1 ? "" : "s"}.
            </p>
            {answer.citations.length > 0 && (
              <div className="space-y-1">
                <p className="text-[11px] font-medium text-muted-foreground">Sources</p>
                {answer.citations.map((c, i) => (
                  <div key={i} className="text-[11px] text-muted-foreground">
                    <span className="mr-1.5 font-mono">[{i + 1}]</span>
                    <span className="mr-1.5 font-mono font-semibold">{c.symbol}</span>
                    {c.url ? (
                      <a href={c.url} target="_blank" rel="noreferrer"
                        className="underline decoration-dotted hover:text-foreground">
                        {c.title}
                      </a>
                    ) : (
                      <span>{c.title}</span>
                    )}
                    <span className="ml-1.5 text-muted-foreground/70">— {c.source}</span>
                    {c.claim && <span className="ml-1.5 italic">“{c.claim}”</span>}
                  </div>
                ))}
              </div>
            )}
          </div>
        )}
      </CardContent>
    </Card>
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
            <Link to={`/research?symbol=${encodeURIComponent(row.symbol)}`}>
              <Button size="sm" variant="ghost">Research</Button>
            </Link>
            <Link to={`/filings?symbol=${encodeURIComponent(row.symbol)}`}>
              <Button size="sm" variant="ghost">Filings</Button>
            </Link>
            <Link to={`/trade?symbol=${encodeURIComponent(row.symbol)}`}>
              <Button size="sm" variant="outline">Trade →</Button>
            </Link>
          </div>
        </div>

        {row.headlines.length > 0 ? (
          <div className="mt-2 space-y-1">
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
            No recent news — in the window purely on its upcoming report.
          </p>
        )}
      </CardContent>
    </Card>
  )
}
