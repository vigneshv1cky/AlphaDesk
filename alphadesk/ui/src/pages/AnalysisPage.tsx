import { useEffect, useState } from "react"
import { useSearchParams } from "react-router-dom"
import { SymbolChart } from "@/components/SymbolChart"
import { SymbolFilings } from "@/components/SymbolFilings"
import { Btn, fieldCls } from "@/components/terminal"
import { normalize, useWatchlist } from "@/lib/watchlist"

/** Everything about ONE company, in one place: the chart and its filings.
 *
 * These used to be two routes with two symbol inputs, which meant the terminal
 * could be showing you NVDA's chart and AAPL's 10-K at the same time. One
 * input at the top scopes the whole page, and it writes ?symbol= to the URL —
 * which is also how the AI rail learns what you are looking at, so asking a
 * question needs no extra step.
 */
export default function AnalysisPage() {
  const [params, setParams] = useSearchParams()
  const urlSymbol = normalize(params.get("symbol") || "") || "AAPL"
  const [query, setQuery] = useState(urlSymbol)
  const { symbols, add, remove } = useWatchlist()
  const watched = symbols.includes(urlSymbol)

  useEffect(() => { setQuery(urlSymbol) }, [urlSymbol])

  const scope = (raw: string) => {
    const sym = normalize(raw)
    if (!sym) return
    const next = new URLSearchParams(params)
    next.set("symbol", sym)
    // A filing selected for the previous company must not survive the switch —
    // the rail would keep answering questions about the wrong document.
    next.delete("accession")
    setParams(next, { replace: true })
  }

  return (
    <div className="collage">
      <form
        className="col-span-12 flex flex-wrap items-center gap-1.5 border-b border-border pb-2"
        onSubmit={e => { e.preventDefault(); scope(query) }}
      >
        <input
          value={query}
          onChange={e => setQuery(e.target.value)}
          placeholder="Symbol"
          aria-label="Symbol to analyse"
          className={`${fieldCls} w-28 font-mono uppercase`}
        />
        <Btn type="submit" variant="accent">Load</Btn>
        <span className="num text-[12px] font-semibold">{urlSymbol}</span>
        <button
          type="button"
          onClick={() => (watched ? remove(urlSymbol) : add(urlSymbol))}
          className={`px-2 py-[3px] text-[11px] transition-colors ${
            watched
              ? "text-accent hover:underline"
              : "border border-border text-muted-foreground hover:bg-muted hover:text-foreground"
          }`}
        >
          {watched ? "★ watching" : "☆ watch"}
        </button>
      </form>

      <SymbolChart symbol={urlSymbol} />
      <SymbolFilings symbol={urlSymbol} />
    </div>
  )
}
