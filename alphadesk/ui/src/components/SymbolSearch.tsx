import { useEffect, useRef, useState } from "react"
import { api, type SymbolHit } from "@/lib/api"
import { useBoardSymbols } from "@/lib/boardSymbols"
import { normalize } from "@/lib/watchlist"

/** Add a symbol to the board's strip and scope the board to it.
 *
 * Opened from a `+` beside the symbol chips, the way theirs is, rather than
 * sitting in the header as a permanent input — the board is usually already
 * scoped, and a search box you are not using is chrome.
 *
 * Picking APPENDS a chip rather than replacing the one already there, so the
 * board accumulates the names you are working through and switching between
 * them is one click instead of one search. Picking a symbol already on the
 * strip just activates that chip.
 *
 * Results come from the cached Alpaca asset list server-side, so it can only
 * ever offer symbols the terminal will actually render. A free-text box would
 * take a ticker that resolves to nothing and leave every tile blank with no
 * explanation.
 *
 * Empty query shows what is currently most active. Theirs calls that "Trending
 * Tickers"; this is the same idea sourced from data we actually have rather
 * than a curated list we would have to invent.
 */
export function SymbolSearch() {
  const { add } = useBoardSymbols()
  const [open, setOpen] = useState(false)
  const [q, setQ] = useState("")
  const [hits, setHits] = useState<SymbolHit[]>([])
  const [trending, setTrending] = useState(true)
  const [active, setActive] = useState(0)
  const box = useRef<HTMLDivElement>(null)
  const input = useRef<HTMLInputElement>(null)

  useEffect(() => { if (open) input.current?.focus() }, [open])

  useEffect(() => {
    if (!open) return
    // Debounced: a request per keystroke would be ten for "microsoft", and only
    // the last one's answer is still wanted.
    const id = setTimeout(() => {
      api.search(q)
        .then(d => { setHits(d.results); setTrending(d.trending); setActive(0) })
        .catch(() => setHits([]))
    }, q ? 140 : 0)
    return () => clearTimeout(id)
  }, [q, open])

  useEffect(() => {
    if (!open) return
    const away = (e: MouseEvent) => {
      if (box.current && !box.current.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener("mousedown", away)
    return () => document.removeEventListener("mousedown", away)
  }, [open])

  const pick = (symbol: string) => {
    add(symbol)          // appends a chip and makes it active; see lib/boardSymbols
    setQ("")
    setOpen(false)
  }

  const onKey = (e: React.KeyboardEvent) => {
    if (e.key === "ArrowDown") { e.preventDefault(); setActive(a => Math.min(a + 1, hits.length - 1)) }
    else if (e.key === "ArrowUp") { e.preventDefault(); setActive(a => Math.max(a - 1, 0)) }
    else if (e.key === "Enter") {
      e.preventDefault()
      // Falls through to the typed text when the list has nothing, so Enter
      // never silently does nothing on a symbol the search does not know.
      if (hits[active]) pick(hits[active].symbol)
      else if (normalize(q)) pick(q)
    }
    else if (e.key === "Escape") { e.preventDefault(); setOpen(false) }
  }

  return (
    <div ref={box} className="relative">
      <button
        type="button"
        onClick={() => setOpen(o => !o)}
        aria-label="Add a symbol"
        // A bare dashed circle does not say "you can search the whole listed
        // universe from here", and the search behind it covers every symbol
        // the terminal accepts — so the hover says so.
        title="Add a symbol — search any ticker or company"
        aria-expanded={open}
        className={`flex h-[22px] w-[22px] items-center justify-center rounded-full border border-dashed text-[13px] transition-colors ${
          open ? "border-accent text-accent" : "border-border text-muted-foreground hover:border-accent hover:text-accent"
        }`}
      >
        +
      </button>

      {open && (
        <div className="absolute left-0 top-full z-50 mt-2 w-[420px] overflow-hidden rounded-lg border border-border bg-popover shadow-xl">
          <input
            ref={input}
            value={q}
            onChange={e => setQ(e.target.value)}
            onKeyDown={onKey}
            placeholder="Search ticker or company…"
            aria-label="Search ticker or company"
            className="w-full border-b border-grid-line bg-transparent px-4 py-3 text-[14px] text-foreground outline-none placeholder:text-muted-foreground"
          />
          {trending && hits.length > 0 && (
            <div className="px-4 pb-1 pt-3 text-[13px] font-semibold">Most active</div>
          )}
          <div className="max-h-[320px] overflow-y-auto">
            {hits.length === 0 ? (
              q ? (
                /* THE ESCAPE HATCH. The list behind this box is Alpaca's
                   tradable universe — 13,396 names covering US equities, ETFs,
                   ADRs and class shares. What it does NOT cover is anything
                   Alpaca will not trade: a foreign listing like NESN, an OTC
                   quote, a symbol listed since the asset cache was built. The
                   chart runs on yfinance, which can price several of those, so
                   a symbol missing from the search is not necessarily one the
                   terminal cannot show — and refusing to add it was the search
                   speaking for the whole app.

                   Adding it here is a real attempt, not a promise: the tiles
                   load it and say so plainly when there is nothing behind it.

                   Index symbols are still out of reach, and not because of
                   this box — `normalize` and nine server-side sanitizers all
                   strip the caret, so ^GSPC arrives as GSPC. Widening that is
                   its own change. */
                <button
                  type="button"
                  onClick={() => pick(q)}
                  className="flex w-full flex-col gap-0.5 px-4 py-3 text-left hover:bg-muted"
                >
                  <span className="text-[14px] font-semibold text-accent">
                    {normalize(q) || q.toUpperCase()}
                  </span>
                  <span className="text-[13px] text-muted-foreground">
                    Not in the tradable list — add it anyway and try to load it
                  </span>
                </button>
              ) : (
                <p className="px-4 py-4 text-[13px] text-muted-foreground">
                  No movers right now.
                </p>
              )
            ) : hits.map((h, i) => (
              <button
                key={h.symbol}
                type="button"
                onMouseEnter={() => setActive(i)}
                onClick={() => pick(h.symbol)}
                className={`flex w-full flex-col gap-0.5 border-b border-grid-line px-4 py-2 text-left last:border-b-0 ${
                  i === active ? "bg-accent text-accent-foreground" : "hover:bg-muted"
                }`}
              >
                <span className="flex items-baseline justify-between gap-3">
                  <span className={`text-[14px] font-semibold ${i === active ? "" : "text-accent"}`}>
                    {h.symbol}
                  </span>
                  <span className={`text-[12px] ${i === active ? "opacity-90" : "text-muted-foreground"}`}>
                    {h.asset_class ?? ""}
                  </span>
                </span>
                <span className="flex items-baseline justify-between gap-3">
                  <span className={`truncate text-[13px] ${i === active ? "opacity-90" : "text-muted-foreground"}`}>
                    {h.name ?? ""}
                  </span>
                  <span className={`shrink-0 text-[12px] ${i === active ? "opacity-90" : "text-muted-foreground"}`}>
                    {h.exchange ?? ""}
                  </span>
                </span>
              </button>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}
