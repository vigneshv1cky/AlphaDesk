import { useState } from "react"
import { api, type ScreenerAnswer } from "@/lib/api"
import { useScreener } from "@/lib/queries"
import { Btn, Empty, Widget, areaCls } from "@/components/terminal"
import { WindowTable } from "@/components/WindowTable"

/** The front door — an inventory of what's in the window, and an AI you ask.
 *
 * Nothing here is ranked. The list is every symbol with fresh news or a
 * report inside the horizon, alphabetical: ordering a list is itself a
 * judgment, and the judgment is the operator's. The AI writes nothing on its
 * own either — ask a question and one call reads the WHOLE window (every
 * article and report, across every symbol) and answers it, citing numbered
 * items resolved server-side back to real stored records. */
export default function ScreenerPage() {
  const { data, error } = useScreener()
  const rows = data?.symbols ?? null
  const err = error ? String(error.message ?? error) : null

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
        {rows !== null && rows.length > 0 && <WindowTable rows={rows} />}
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


