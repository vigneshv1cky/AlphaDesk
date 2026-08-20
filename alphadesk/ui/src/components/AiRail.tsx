import { useEffect, useMemo, useRef, useState } from "react"
import { useLocation, useSearchParams } from "react-router-dom"
import { api, type FilingRow } from "@/lib/api"
import { Btn, Empty, Tag, areaCls, fieldCls } from "@/components/terminal"
import { cn } from "@/lib/utils"

/** The AI rail — the ONE place the terminal answers questions.
 *
 * Previously the AI lived in three page-local boxes (screener ask, filing ask,
 * research ask), each stranded on its own route: to ask about a symbol you had
 * to navigate away from whatever you were looking at. This is the Copilot
 * shape instead — one collapsible panel, present on every route, that follows
 * your context rather than making you go to it.
 *
 * Context comes from the URL, deliberately: `?symbol=` and `?accession=` are
 * already how the pages talk to each other, so the rail needs no shared store
 * and a link into a page arrives with the rail already pointed at the right
 * thing.
 *
 * Every answer still renders its citations, and those citations are still
 * resolved server-side against records we control — moving the question box
 * does not relax the attribution rule.
 */

type Mode = "window" | "symbol" | "filing"

type Turn = {
  mode: Mode
  scope: string
  question: string
  answer: string
  cites: { label: string; url?: string; claim?: string }[]
  note?: string
}

const MODES: { id: Mode; label: string; blurb: string }[] = [
  { id: "window", label: "Window", blurb: "every article + upcoming report, at once" },
  { id: "symbol", label: "Symbol", blurb: "fundamentals, ownership, insiders, macro, sector" },
  { id: "filing", label: "Filing", blurb: "one SEC document, answered in verbatim quotes" },
]

export function AiRail() {
  const { pathname } = useLocation()
  const [params] = useSearchParams()
  const urlSymbol = (params.get("symbol") || "").toUpperCase()
  const urlAccession = params.get("accession") || ""

  const [open, setOpen] = useState(() => {
    try { return localStorage.getItem("ai-rail") !== "0" } catch { return true }
  })
  useEffect(() => {
    try { localStorage.setItem("ai-rail", open ? "1" : "0") } catch { /* private mode */ }
  }, [open])

  // The rail follows the page unless you've picked a mode yourself.
  const suggested: Mode = urlAccession ? "filing" : urlSymbol ? "symbol" : "window"
  const [pinned, setPinned] = useState<Mode | null>(null)
  const mode = pinned ?? suggested
  useEffect(() => { setPinned(null) }, [pathname])

  const [symbol, setSymbol] = useState(urlSymbol)
  useEffect(() => { if (urlSymbol) setSymbol(urlSymbol) }, [urlSymbol])

  const [accession, setAccession] = useState(urlAccession)
  useEffect(() => { if (urlAccession) setAccession(urlAccession) }, [urlAccession])

  // Filing mode needs a document to ask about; fetch the symbol's list so the
  // rail can stand alone instead of depending on the Filings page selection.
  const [filings, setFilings] = useState<FilingRow[]>([])
  useEffect(() => {
    if (mode !== "filing" || !symbol) return
    let alive = true
    api.filings(symbol).then(d => { if (alive) setFilings(d.filings) }).catch(() => {})
    return () => { alive = false }
  }, [mode, symbol])

  const [question, setQuestion] = useState("")
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const [turns, setTurns] = useState<Turn[]>([])
  const endRef = useRef<HTMLDivElement>(null)
  useEffect(() => { endRef.current?.scrollIntoView({ block: "end" }) }, [turns, busy])

  const ready = useMemo(() => {
    if (!question.trim() || busy) return false
    if (mode === "symbol") return !!symbol.trim()
    if (mode === "filing") return !!accession
    return true
  }, [question, busy, mode, symbol, accession])

  const ask = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!ready) return
    const q = question.trim()
    setBusy(true); setErr(null)
    try {
      let turn: Turn
      if (mode === "window") {
        const r = await api.askScreener(q)
        turn = {
          mode, scope: "window", question: q, answer: r.answer,
          cites: r.citations.map(c => ({ label: `${c.symbol} · ${c.title}`, url: c.url || undefined, claim: c.claim })),
          note: `read ${r.considered.articles} articles + ${r.considered.earnings} reports across ${r.considered.symbols} symbols`,
        }
      } else if (mode === "symbol") {
        const sym = symbol.trim().toUpperCase()
        const r = await api.askResearch(sym, q)
        turn = {
          mode, scope: sym, question: q, answer: r.answer,
          cites: r.citations.map(c => ({ label: c.title, claim: c.claim })),
          note: r.citations.length ? undefined : "no claim could be tied to a fetched section — treat with caution",
        }
      } else {
        const r = await api.askFiling(accession, q)
        const f = filings.find(x => x.accession === accession)
        turn = {
          mode, scope: f ? `${f.form} ${f.filing_date}` : accession, question: q, answer: r.answer,
          cites: r.citations.map(c => ({ label: `“${c.quote}”` })),
          note: r.citations.length ? undefined : "no quote verified against the filing text — treat with caution",
        }
      }
      setTurns(t => [...t, turn])
      setQuestion("")
    } catch (e2: unknown) {
      setErr(String((e2 as Error)?.message ?? e2))
    } finally {
      setBusy(false)
    }
  }

  const Collapsed = (
    <button
      onClick={() => setOpen(true)}
      title="Open the AI panel"
      className={cn(
        "w-[28px] shrink-0 flex-col items-center gap-2 border-l border-border bg-panel py-2 text-[12px] uppercase tracking-[0.12em] text-muted-foreground hover:text-foreground",
        // Below xl the canvas cannot afford a 360px rail — sidebar + rail
        // would leave the data less room than the chrome. Collapsed is forced
        // there regardless of the stored preference.
        open ? "flex xl:hidden" : "flex",
      )}
    >
      <span>◀</span>
      <span style={{ writingMode: "vertical-rl" }}>Ask</span>
    </button>
  )

  if (!open) return Collapsed

  return (
    <>
    {Collapsed}
    <aside className="hidden w-[360px] shrink-0 flex-col border-l border-border bg-background xl:flex">
      <header className="flex h-[42px] shrink-0 items-center gap-2 px-3">
        <span className="text-[16px] text-accent">✦</span>
        <h2 className="text-[15px] font-semibold uppercase tracking-[0.06em]">Ask AlphaDesk</h2>
        <div className="flex-1" />
        {turns.length > 0 && <Btn variant="ghost" onClick={() => setTurns([])}>clear</Btn>}
        <Btn variant="ghost" onClick={() => setOpen(false)} title="Collapse">▶</Btn>
      </header>

      <div className="flex shrink-0 border-b border-border">
        {MODES.map(m => (
          <button
            key={m.id}
            onClick={() => setPinned(m.id)}
            title={m.blurb}
            className={`flex-1 border-b-2 px-1 py-1 text-[12px] font-medium uppercase tracking-[0.04em] transition-colors ${
              mode === m.id
                ? "border-accent text-foreground"
                : "border-transparent text-muted-foreground hover:bg-muted hover:text-foreground"
            }`}
          >
            {m.label}
          </button>
        ))}
      </div>

      {(mode === "symbol" || mode === "filing") && (
        <div className="flex shrink-0 items-center gap-1 border-b border-border p-1">
          <input
            value={symbol}
            onChange={e => setSymbol(e.target.value.toUpperCase())}
            placeholder="Symbol"
            className={`${fieldCls} w-20 font-mono uppercase`}
          />
          {mode === "filing" && (
            <select
              value={accession}
              onChange={e => setAccession(e.target.value)}
              className={`${fieldCls} min-w-0 flex-1`}
            >
              <option value="">{filings.length ? "pick a filing…" : "load a symbol first"}</option>
              {filings.map(f => (
                <option key={f.accession} value={f.accession}>
                  {f.form} · {f.filing_date}
                </option>
              ))}
            </select>
          )}
        </div>
      )}

      <div className="min-h-0 flex-1 overflow-y-auto">
        {turns.length === 0 && !busy && (
          <div className="flex h-full flex-col justify-end p-3">
            <h3 className="text-[20px] font-semibold leading-tight">
              What would you<br />like to know?
            </h3>
            <p className="mt-1.5 text-[14px] text-muted-foreground">
              {mode === "window"
                ? "Reads every article and upcoming report at once."
                : mode === "symbol"
                  ? "Fundamentals, ownership, insiders, macro and sector for one symbol."
                  : "One SEC filing. Answers are verbatim quotes, verified."}
            </p>
            <div className="mt-3 space-y-2">
              {(mode === "window"
                ? ["What's the biggest news in the window?",
                   "Anything unusual before earnings this week?",
                   "Which sectors are showing up most?"]
                : mode === "symbol"
                  ? ["Is institutional ownership growing?",
                     "Any recent insider selling?",
                     "How did the last earnings land?"]
                  : ["What did they say about margins?",
                     "Any new risk factors?",
                     "What changed since last quarter?"]
              ).map(q => (
                <button
                  key={q}
                  onClick={() => setQuestion(q)}
                  className="block w-full rounded-full border border-border px-3 py-2 text-left text-[14px] text-foreground/90 transition-colors hover:bg-muted"
                >
                  {q}
                </button>
              ))}
            </div>
          </div>
        )}
        {turns.map((t, i) => (
          <div key={i} className="border-b border-grid-line px-2 py-1.5">
            <div className="mb-1 flex items-center gap-1.5">
              <Tag tone="accent">{t.scope}</Tag>
              <span className="truncate text-[14px] font-medium">{t.question}</span>
            </div>
            <p className="whitespace-pre-wrap text-[14px] leading-snug">{t.answer}</p>
            {t.note && <p className="mt-1 text-[12px] text-muted-foreground">{t.note}</p>}
            {t.cites.length > 0 && (
              <div className="mt-1 space-y-0.5 border-t border-grid-line pt-1">
                {t.cites.map((c, j) => (
                  <div key={j} className="text-[12px] text-muted-foreground">
                    <span className="num mr-1">[{j + 1}]</span>
                    {c.url ? (
                      <a href={c.url} target="_blank" rel="noreferrer" className="underline decoration-dotted hover:text-foreground">
                        {c.label}
                      </a>
                    ) : (
                      <span>{c.label}</span>
                    )}
                    {c.claim && <span className="ml-1 italic">— {c.claim}</span>}
                  </div>
                ))}
              </div>
            )}
          </div>
        ))}
        {busy && <Empty>thinking…</Empty>}
        {err && <div className="px-2 py-1 text-[14px] text-loss">{err}</div>}
        <div ref={endRef} />
      </div>

      <form onSubmit={ask} className="shrink-0 border-t border-border p-1">
        <textarea
          value={question}
          onChange={e => setQuestion(e.target.value)}
          onKeyDown={e => {
            // Enter sends, Shift+Enter newlines — chat convention, and the box
            // is two rows so a stray Enter would otherwise be invisible.
            if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); ask(e as unknown as React.FormEvent) }
          }}
          rows={2}
          placeholder={
            mode === "window" ? "What's worth looking at right now?"
              : mode === "symbol" ? "Any recent insider selling?"
                : "What did they say about margins?"
          }
          className={`${areaCls} w-full`}
        />
        <div className="mt-1 flex items-center gap-1">
          <span className="truncate text-[11px] text-muted-foreground">
            AI can make mistakes. Every claim is cited — check the sources.
          </span>
          <div className="flex-1" />
          <button
            type="submit"
            disabled={!ready}
            aria-label="Ask"
            className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-accent text-accent-foreground transition-opacity hover:opacity-90 disabled:opacity-30"
          >
            {busy ? "…" : "↑"}
          </button>
        </div>
      </form>
    </aside>
    </>
  )
}
