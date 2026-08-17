import { useEffect, useState } from "react"
import { useSearchParams } from "react-router-dom"
import { api, type FilingAnswer, type FilingRow } from "@/lib/api"
import { Card, CardContent } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"

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
    <div className="space-y-4">
      <div>
        <h1 className="text-lg font-bold tracking-tight">Filings</h1>
        <p className="text-xs text-muted-foreground">
          SEC EDGAR, straight from the source — free, complete, no vendor. Ask a
          filing a question; every answer is a verbatim quote from the actual document,
          never the model's paraphrase passed off as fact.
        </p>
      </div>

      <form
        className="flex flex-wrap items-center gap-2"
        onSubmit={e => { e.preventDefault(); load(query.trim().toUpperCase()) }}
      >
        <input
          value={query}
          onChange={e => setQuery(e.target.value)}
          placeholder="Symbol"
          className="h-9 w-32 rounded-md border bg-background px-3 text-sm font-mono uppercase"
        />
        <Button type="submit" size="sm" disabled={loading}>
          {loading ? "Loading…" : "Load"}
        </Button>
        {err && <span className="text-sm text-red-600">{err}</span>}
      </form>

      {filings && (
        <div className="grid gap-4 lg:grid-cols-[280px_1fr]">
          <div className="space-y-1.5">
            {filings.length === 0 ? (
              <Card className="border-dashed"><CardContent className="py-6 text-center text-sm text-muted-foreground">
                No filings found for {symbol}.
              </CardContent></Card>
            ) : filings.map(f => (
              <button
                key={f.accession}
                onClick={() => setSelected(f)}
                className={`block w-full rounded-lg border p-2.5 text-left text-sm transition-colors ${
                  selected?.accession === f.accession
                    ? "border-indigo-500 bg-indigo-500/5"
                    : "hover:bg-muted"
                }`}
              >
                <div className="flex items-center justify-between">
                  <Badge variant="secondary">{f.form}</Badge>
                  <span className="text-xs text-muted-foreground">{f.filing_date}</span>
                </div>
              </button>
            ))}
          </div>

          <div>
            {selected ? (
              <FilingReader filing={selected} />
            ) : (
              <Card className="border-dashed"><CardContent className="py-10 text-center text-sm text-muted-foreground">
                Pick a filing on the left to read it or ask it something.
              </CardContent></Card>
            )}
          </div>
        </div>
      )}
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
    <div className="space-y-3">
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

      <form onSubmit={ask} className="flex gap-2">
        <input
          value={question}
          onChange={e => setQuestion(e.target.value)}
          placeholder="What did they say about margins, buybacks, litigation…?"
          className="h-9 flex-1 rounded-md border bg-background px-3 text-sm"
        />
        <Button type="submit" size="sm" disabled={asking || !question.trim()}>
          {asking ? "Asking…" : "Ask"}
        </Button>
      </form>

      {err && <p className="text-sm text-red-600">{err}</p>}

      {answer && (
        <Card><CardContent className="space-y-2 py-4">
          <p className="text-sm">{answer.answer}</p>
          {answer.citations.length > 0 ? (
            <div className="space-y-1.5 border-t border-border pt-2">
              <p className="text-[10px] font-medium uppercase tracking-widest text-muted-foreground">
                Verbatim, from the filing
              </p>
              {answer.citations.map((c, i) => (
                <blockquote key={i} className="border-l-2 border-indigo-500/40 pl-2 text-xs text-muted-foreground">
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
        </CardContent></Card>
      )}
    </div>
  )
}
