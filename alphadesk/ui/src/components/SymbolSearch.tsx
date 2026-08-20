import { useEffect, useRef, useState } from "react"
import { useSearchParams } from "react-router-dom"
import { api } from "@/lib/api"
import { fieldCls } from "@/components/terminal"

/** Pick the symbol the whole board is scoped to.
 *
 * Until this existed the only way to scope the board was to edit ?symbol= by
 * hand or click through from a movers row — the chart tile said "pick a
 * symbol" and offered no way to do it.
 *
 * Searches the cached Alpaca asset list server-side, so it can only ever offer
 * symbols the terminal will actually accept. A free-text box would happily
 * take a ticker that resolves to nothing and leave every tile empty with no
 * explanation.
 */
export function SymbolSearch() {
  const [params, setParams] = useSearchParams()
  const [q, setQ] = useState("")
  const [results, setResults] = useState<{ symbol: string; name: string | null }[]>([])
  const [open, setOpen] = useState(false)
  const [active, setActive] = useState(0)
  const box = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!q.trim()) { setResults([]); return }
    // Debounced: a keystroke per character would be ~10 requests for "microsoft"
    // and the last one is the only one whose answer is still wanted.
    const id = setTimeout(() => {
      api.search(q).then(d => { setResults(d.results); setActive(0) }).catch(() => setResults([]))
    }, 140)
    return () => clearTimeout(id)
  }, [q])

  useEffect(() => {
    const away = (e: MouseEvent) => {
      if (box.current && !box.current.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener("mousedown", away)
    return () => document.removeEventListener("mousedown", away)
  }, [])

  const pick = (symbol: string) => {
    const next = new URLSearchParams(params)
    next.set("symbol", symbol)
    // A filing chosen for the previous company must not survive the switch.
    next.delete("accession")
    setParams(next, { replace: true })
    setQ("")
    setOpen(false)
  }

  const onKey = (e: React.KeyboardEvent) => {
    if (e.key === "ArrowDown") { e.preventDefault(); setActive(a => Math.min(a + 1, results.length - 1)) }
    else if (e.key === "ArrowUp") { e.preventDefault(); setActive(a => Math.max(a - 1, 0)) }
    else if (e.key === "Enter" && results[active]) { e.preventDefault(); pick(results[active].symbol) }
    else if (e.key === "Escape") setOpen(false)
  }

  return (
    <div ref={box} className="relative">
      <input
        value={q}
        onChange={e => { setQ(e.target.value); setOpen(true) }}
        onFocus={() => setOpen(true)}
        onKeyDown={onKey}
        placeholder="Search symbol"
        aria-label="Search for a symbol to scope this board"
        className={`${fieldCls} w-[190px]`}
      />
      {open && results.length > 0 && (
        <div className="absolute left-0 top-full z-50 mt-1 max-h-[320px] w-[320px] overflow-y-auto rounded-md border border-border bg-popover py-1 shadow-lg">
          {results.map((r, i) => (
            <button
              key={r.symbol}
              type="button"
              onMouseEnter={() => setActive(i)}
              onClick={() => pick(r.symbol)}
              className={`flex w-full items-baseline gap-2 px-3 py-[5px] text-left ${
                i === active ? "bg-muted" : ""
              }`}
            >
              <span className="w-[64px] shrink-0 text-[14px] font-medium">{r.symbol}</span>
              <span className="truncate text-[12px] text-muted-foreground">{r.name ?? ""}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  )
}
