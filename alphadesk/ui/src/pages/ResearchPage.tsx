import { useState } from "react"
import { useSearchParams } from "react-router-dom"
import { api, type ResearchAnswer } from "@/lib/api"
import { Card, CardContent } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible"
import { ChevronDown } from "lucide-react"

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
    <div className="space-y-4">
      <div>
        <h1 className="text-lg font-bold tracking-tight">Research</h1>
        <p className="text-xs text-muted-foreground">
          Ask a question about one symbol — fundamentals, institutional ownership, insider
          trades, earnings history, macro conditions, and sector performance are all pulled
          first; the answer (and any suggestion) is grounded only in that data, cited by section.
        </p>
      </div>

      <form onSubmit={ask} className="flex flex-col gap-2 sm:flex-row sm:items-start">
        <input
          value={symbol}
          onChange={e => setSymbol(e.target.value)}
          placeholder="Symbol"
          className="h-9 w-28 shrink-0 rounded-md border bg-background px-3 text-sm font-mono uppercase"
        />
        <textarea
          value={question}
          onChange={e => setQuestion(e.target.value)}
          placeholder="e.g. Is institutional ownership growing? Any recent insider selling? What's your take?"
          rows={2}
          className="h-auto min-h-[2.5rem] flex-1 resize-y rounded-md border bg-background px-3 py-2 text-sm"
        />
        <Button type="submit" size="sm" disabled={asking || !symbol.trim() || !question.trim()} className="shrink-0">
          {asking ? "Researching…" : "Ask"}
        </Button>
      </form>

      {err && <p className="text-sm text-red-600">{err}</p>}

      {answer && (
        <Card>
          <CardContent className="space-y-3 py-4">
            <p className="whitespace-pre-wrap text-sm">{answer.answer}</p>

            {answer.citations.length > 0 ? (
              <div className="space-y-1.5 border-t border-border pt-2">
                <p className="text-[10px] font-medium uppercase tracking-widest text-muted-foreground">
                  Grounded in
                </p>
                {answer.citations.map((c, i) => (
                  <div key={i} className="flex flex-wrap items-start gap-1.5 text-xs text-muted-foreground">
                    <span>{c.claim}</span>
                    <span className="font-mono text-[11px] text-indigo-500/80">via {c.title}</span>
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
              <Collapsible open={trailOpen} onOpenChange={setTrailOpen} className="border-t border-border pt-2">
                <CollapsibleTrigger className="flex items-center gap-1 text-[10px] font-medium uppercase tracking-widest text-muted-foreground hover:text-foreground">
                  <ChevronDown className={`h-3 w-3 transition-transform ${trailOpen ? "rotate-180" : ""}`} />
                  Data used ({answer.sections.length} section{answer.sections.length === 1 ? "" : "s"})
                </CollapsibleTrigger>
                <CollapsibleContent className="mt-2 space-y-2">
                  {answer.sections.map((s, i) => (
                    <div key={i} className="rounded-md border border-border p-2">
                      <Badge variant="secondary" className="font-mono text-[10px]">{s.title}</Badge>
                      <pre className="mt-1.5 max-h-40 overflow-auto rounded bg-muted p-1.5 text-[10px] text-muted-foreground">
                        {JSON.stringify(s.data, null, 2)}
                      </pre>
                    </div>
                  ))}
                </CollapsibleContent>
              </Collapsible>
            )}
          </CardContent>
        </Card>
      )}
    </div>
  )
}
