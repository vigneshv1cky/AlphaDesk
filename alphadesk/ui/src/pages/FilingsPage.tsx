import { useEffect, useState } from "react"
import { useSearchParams } from "react-router-dom"
import { api, type FilingAnswer, type FilingRow } from "@/lib/api"
import { Badge, Btn, Empty, Widget, fieldCls } from "@/components/terminal"

/** SEC filings, straight from EDGAR — free, no vendor, no API key. Pick a
 * filing, ask it a question. Every answer is backed only by verbatim quotes
 * checked against the actual document text server-side; the model can't
 * fabricate a citation that doesn't literally appear in the SEC filing. */
export default function FilingsPage() {
  const [params] = useSearchParams()
  const [query, setQuery] = useState((params.get("symbol") || "AAPL").toUpperCase())
  const [symbol, setSymbol] = useState("")
  const [filings, setFilings] = useState<FilingRow[] | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const [selected, setSelected] = useState<FilingRow | null>(null)

  const load = (sym: string) => {
    if (!sym) return
    setLoading(true)
    setErr(null)
    setSelected(null)
    api.filings(sym)
      .then(d => { setFilings(d.filings); setSymbol(d.symbol) })
      .catch(e => { setFilings(null); setErr(String(e.message ?? e)) })
      .finally(() => setLoading(false))
  }

  useEffect(() => { load(query) }, []) // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <div className="collage">
      <Widget
        span={12}
        title="Filings"
        subtitle="SEC EDGAR direct — every answer is a verbatim quote from the document, never a paraphrase passed off as fact"
      >
      <form
        className="flex flex-wrap items-center gap-1.5 border-b border-border p-1"
        onSubmit={e => { e.preventDefault(); load(query.trim().toUpperCase()) }}
      >
        <input
          value={query}
          onChange={e => setQuery(e.target.value)}
          placeholder="Symbol"
          className={`${fieldCls} w-24 font-mono uppercase`}
        />
        <Btn type="submit" variant="accent" disabled={loading}>
          {loading ? "Loading…" : "Load"}
        </Btn>
        {err && <span className="text-[11px] text-loss">{err}</span>}
      </form>

      {filings && (
        <div className="grid lg:grid-cols-[220px_1fr]">
          <div className="border-r border-border">
            {filings.length === 0 ? (
              <Empty>No filings found for {symbol}.</Empty>
            ) : filings.map(f => (
              <button
                key={f.accession}
                onClick={() => setSelected(f)}
                className={`flex w-full items-center justify-between gap-2 border-b border-grid-line px-2 py-1 text-left transition-colors ${
                  selected?.accession === f.accession
                    ? "bg-accent/15 text-foreground"
                    : "hover:bg-muted"
                }`}
              >
                <Badge variant={selected?.accession === f.accession ? "default" : "secondary"}>{f.form}</Badge>
                <span className="num text-[10px] text-muted-foreground">{f.filing_date}</span>
              </button>
            ))}
          </div>

          <div className="min-w-0">
            {selected ? (
              <FilingReader filing={selected} />
            ) : (
              <Empty>Pick a filing on the left to read it or ask it something.</Empty>
            )}
          </div>
        </div>
      )}
      </Widget>
    </div>
  )
}

function FilingReader({ filing }: { filing: FilingRow }) {
  const [question, setQuestion] = useState("")
  const [answer, setAnswer] = useState<FilingAnswer | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [asking, setAsking] = useState(false)

  useEffect(() => { setAnswer(null); setErr(null); setQuestion("") }, [filing.accession])

  const ask = (e: React.FormEvent) => {
    e.preventDefault()
    if (!question.trim()) return
    setAsking(true)
    setErr(null)
    api.askFiling(filing.accession, question.trim())
      .then(setAnswer)
      .catch(e => setErr(String(e.message ?? e)))
      .finally(() => setAsking(false))
  }

  return (
    <div className="space-y-1">
      <div className="flex items-center justify-between">
        <div>
          <span className="text-sm font-semibold">{filing.symbol} {filing.form}</span>
          <span className="ml-2 text-xs text-muted-foreground">filed {filing.filing_date}</span>
        </div>
        <a href={filing.url} target="_blank" rel="noreferrer"
          className="text-xs text-muted-foreground underline decoration-dotted hover:text-foreground">
          Original on sec.gov ↗
        </a>
      </div>

      <form onSubmit={ask} className="flex gap-1.5 px-2 pb-1">
        <input
          value={question}
          onChange={e => setQuestion(e.target.value)}
          placeholder="What did they say about margins, buybacks, litigation…?"
          className={`${fieldCls} flex-1`}
        />
        <Btn type="submit" variant="accent" disabled={asking || !question.trim()}>
          {asking ? "Asking…" : "Ask"}
        </Btn>
      </form>

      {err && <p className="px-2 text-[11px] text-loss">{err}</p>}

      {answer && (
        <div className="space-y-1.5 border-t border-border p-2">
          <p className="text-[12px] leading-snug">{answer.answer}</p>
          {answer.citations.length > 0 ? (
            <div className="space-y-1.5 border-t border-border pt-2">
              <p className="text-[10px] font-medium uppercase tracking-widest text-muted-foreground">
                Verbatim, from the filing
              </p>
              {answer.citations.map((c, i) => (
                <blockquote key={i} className="border-l-2 border-accent/50 pl-2 text-[11px] text-muted-foreground">
                  “{c.quote}”
                </blockquote>
              ))}
            </div>
          ) : (
            <p className="text-[11px] text-amber-700 dark:text-amber-500">
              No quote from this answer could be verified against the filing text —
              treat it with caution.
            </p>
          )}
        </div>
      )}
    </div>
  )
}
