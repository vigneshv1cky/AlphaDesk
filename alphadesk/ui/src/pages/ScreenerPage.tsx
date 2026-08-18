import { useEffect, useState } from "react"
import { Link } from "react-router-dom"
import { api, type ScreenerAnswer, type ScreenerRow } from "@/lib/api"
import { Btn, Empty, Tag, Widget, areaCls } from "@/components/terminal"

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
    <div className="collage">
      <AskBox />

      <Widget
        span={12}
        title="Window"
        subtitle={
          rows === null
            ? "loading…"
            : `${rows.length} symbols · ${withNews} with news · ${reporting} reporting soon — alphabetical, nothing ranked`
        }
      >
        {err && <Empty>{err}</Empty>}
        {rows === null && !err && <Empty>loading…</Empty>}
        {rows !== null && rows.length === 0 && (
          <Empty>nothing in the window — no upcoming earnings and no fresh news</Empty>
        )}
        {rows?.map(r => <ScreenerRowItem key={r.symbol} row={r} />)}
      </Widget>
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
    <Widget span={12} title="Ask the window" subtitle="one call over every article and upcoming report at once">
      <div className="space-y-2 p-2">
        <form onSubmit={ask} className="flex flex-col gap-1.5 sm:flex-row sm:items-start">
          <textarea
            value={question}
            onChange={e => setQuestion(e.target.value)}
            placeholder="e.g. What's the biggest news in the window? Anything unusual before earnings this week?"
            rows={2}
            className={`flex-1 ${areaCls}`}
          />
          <Btn type="submit" variant="accent" disabled={asking || !question.trim()} className="shrink-0">
            {asking ? "Reading…" : "Ask"}
          </Btn>
        </form>

        {err && <p className="text-[11px] text-loss">{err}</p>}

        {answer && (
          <div className="space-y-1.5 border-t border-border pt-2">
            <p className="whitespace-pre-wrap text-[12px] leading-snug">{answer.answer}</p>
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
      </div>
    </Widget>
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

function ScreenerRowItem({ row }: { row: ScreenerRow }) {
  const due = daysUntil(row.report_date)
  return (
    <div className="border-b border-grid-line px-2 py-1 last:border-b-0 hover:bg-muted/40">
      <div className="flex flex-wrap items-center gap-1.5">
        <span className="num text-[12px] font-bold">{row.symbol}</span>
        {due && <Tag tone="accent">{due}</Tag>}
        {row.article_count > 0 && (
          <span className="num text-[10px] text-muted-foreground">{row.article_count} art</span>
        )}
        <div className="flex-1" />
        <Link to={`/research?symbol=${encodeURIComponent(row.symbol)}`}><Btn variant="ghost">research</Btn></Link>
        <Link to={`/filings?symbol=${encodeURIComponent(row.symbol)}`}><Btn variant="ghost">filings</Btn></Link>
        <Link to={`/trade?symbol=${encodeURIComponent(row.symbol)}`}><Btn>trade →</Btn></Link>
      </div>
      {row.headlines.length > 0 && (
        <div className="mt-0.5 space-y-0.5">
          {row.headlines.slice(0, 3).map((h, i) => (
            <a key={i} href={h.url} target="_blank" rel="noreferrer"
              className="block truncate text-[11px] text-muted-foreground hover:text-foreground hover:underline">
              {h.title}
              <span className="ml-1.5 text-[10px] text-muted-foreground/70">— {h.source}</span>
            </a>
          ))}
        </div>
      )}
    </div>
  )
}
