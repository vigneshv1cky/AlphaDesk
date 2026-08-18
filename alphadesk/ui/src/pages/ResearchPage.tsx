import { useState } from "react"
import { useSearchParams } from "react-router-dom"
import { api, type ResearchAnswer } from "@/lib/api"
import { ChevronDown } from "lucide-react"
import { Badge, Btn, Widget, areaCls, fieldCls } from "@/components/terminal"

/** Research over one symbol — its fundamentals, institutional ownership,
 * insider trades, earnings history, macro conditions, and sector
 * performance are all fetched up front, then one AI call answers the
 * question from exactly that data. Every claim cites a real, server-fetched
 * section (the "Data used" trail below), never the model's unverified
 * say-so. */
export default function ResearchPage() {
  const [params] = useSearchParams()
  const [symbol, setSymbol] = useState((params.get("symbol") || "").toUpperCase())
  const [question, setQuestion] = useState("")
  const [answer, setAnswer] = useState<ResearchAnswer | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [asking, setAsking] = useState(false)
  const [trailOpen, setTrailOpen] = useState(false)

  const ask = (e: React.FormEvent) => {
    e.preventDefault()
    if (!symbol.trim() || !question.trim()) return
    setAsking(true)
    setErr(null)
    setAnswer(null)
    setTrailOpen(false)
    api.askResearch(symbol.trim().toUpperCase(), question.trim())
      .then(setAnswer)
      .catch(e => setErr(String(e.message ?? e)))
      .finally(() => setAsking(false))
  }

  return (
    <div className="collage">
      <Widget
        span={12}
        title="Research"
        subtitle="one symbol · fundamentals, ownership, insider trades, earnings, macro, sector — all pre-fetched, answer cited by section"
      >
      <form onSubmit={ask} className="flex flex-col gap-1.5 border-b border-border p-1 sm:flex-row sm:items-start">
        <input
          value={symbol}
          onChange={e => setSymbol(e.target.value)}
          placeholder="Symbol"
          className={`${fieldCls} w-24 shrink-0 font-mono uppercase`}
        />
        <textarea
          value={question}
          onChange={e => setQuestion(e.target.value)}
          placeholder="e.g. Is institutional ownership growing? Any recent insider selling? What's your take?"
          rows={2}
          className={`${areaCls} flex-1`}
        />
        <Btn type="submit" variant="accent" disabled={asking || !symbol.trim() || !question.trim()} className="shrink-0">
          {asking ? "Researching…" : "Ask"}
        </Btn>
      </form>

      {err && <p className="p-2 text-[11px] text-loss">{err}</p>}

      {answer && (
        <div className="space-y-2 p-2">
            <p className="whitespace-pre-wrap text-[12px] leading-snug">{answer.answer}</p>

            {answer.citations.length > 0 ? (
              <div className="space-y-1.5 border-t border-border pt-2">
                <p className="text-[10px] font-medium uppercase tracking-widest text-muted-foreground">
                  Grounded in
                </p>
                {answer.citations.map((c, i) => (
                  <div key={i} className="flex flex-wrap items-start gap-1.5 text-xs text-muted-foreground">
                    <span>{c.claim}</span>
                    <span className="num text-[10px] text-accent">via {c.title}</span>
                  </div>
                ))}
              </div>
            ) : (
              <p className="text-[11px] text-amber-700 dark:text-amber-500">
                No claim in this answer could be tied to a verified data section —
                treat it with caution.
              </p>
            )}

            {answer.sections.length > 0 && (
              <div className="border-t border-border pt-1.5">
                <button
                  type="button"
                  onClick={() => setTrailOpen(o => !o)}
                  aria-expanded={trailOpen}
                  className="flex items-center gap-1 text-[10px] font-medium uppercase tracking-[0.08em] text-muted-foreground hover:text-foreground"
                >
                  <ChevronDown className={`h-3 w-3 transition-transform ${trailOpen ? "rotate-180" : ""}`} />
                  Data used ({answer.sections.length} section{answer.sections.length === 1 ? "" : "s"})
                </button>
                {trailOpen && (
                  <div className="mt-1.5 space-y-1.5">
                    {answer.sections.map((s, i) => (
                      <div key={i} className="border border-border p-1.5">
                        <Badge variant="secondary">{s.title}</Badge>
                        <pre className="num mt-1 max-h-40 overflow-auto bg-muted p-1.5 text-[10px] text-muted-foreground">
                          {JSON.stringify(s.data, null, 2)}
                        </pre>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            )}
        </div>
      )}
      </Widget>
    </div>
  )
}
