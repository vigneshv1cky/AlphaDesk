import { useCallback, useMemo } from "react"
import { useSearchParams } from "react-router-dom"
import { normalize } from "@/lib/watchlist"

/** The symbol strip above the Markets board.
 *
 * Two params, one invariant. `?symbols=` is the strip, in the order you added
 * them; `?symbol=` is the one that is ACTIVE, and the active symbol is the
 * only one the board renders — chart, equity overview and the AI rail all
 * follow it. The strip is a tab bar, not a multi-chart layout: a second chip
 * is a second thing you can switch to, not a second chart drawn at once.
 *
 * Keeping the active symbol in `?symbol=` rather than as "whichever is first
 * in `?symbols=`" is what lets every existing link keep working untouched. A
 * movers row and an earnings row both link with `?symbol=NVDA` alone, and that
 * arrives here as a one-chip strip with NVDA active — no redirect, no
 * migration. It also means activating a chip does not reorder the strip, so
 * chips never jump out from under the cursor.
 *
 * Deliberately URL state, not localStorage: `?symbol=` is already how the
 * pages and the AI rail talk to each other, and a board you can send someone
 * is worth more than one that quietly restores itself. The watchlist is
 * browser-local because it is personal; a strip of tabs is just a view.
 */
export function useBoardSymbols() {
  const [params, setParams] = useSearchParams()

  const active = normalize(params.get("symbol") || "")
  const raw = params.get("symbols") || ""

  // Memoized on the two raw param strings, not rebuilt per render: every
  // callback below closes over this array, and a fresh identity each render
  // would make all of them new too — which re-renders the search popover and
  // every chip on any unrelated param change.
  const symbols = useMemo(() => {
    const listed = raw.split(",").map(normalize).filter(Boolean)
    // An inbound link carrying only `?symbol=` is a strip of one. Deduplicated,
    // because the same ticker twice would render two chips that activate the
    // same board — indistinguishable, and one of them un-closable in practice.
    return [...new Set(active && !listed.includes(active) ? [active, ...listed] : listed)]
  }, [raw, active])

  const commit = useCallback((next: string[], nextActive: string) => {
    const p = new URLSearchParams(params)
    if (next.length) p.set("symbols", next.join(","))
    else p.delete("symbols")
    if (nextActive) p.set("symbol", nextActive)
    else p.delete("symbol")
    // A filing chosen for the previous company must not survive a switch to a
    // different one — but closing some other chip is not a switch, so the
    // selection only clears when the ACTIVE symbol actually changes.
    if (nextActive !== normalize(params.get("symbol") || "")) p.delete("accession")
    setParams(p, { replace: true })
  }, [params, setParams])

  /** Add a symbol to the strip and make it active. Adding one already on the
   * strip just activates it — a second identical chip is never what was
   * meant. */
  const add = useCallback((raw: string) => {
    const sym = normalize(raw)
    if (!sym) return
    commit(symbols.includes(sym) ? symbols : [...symbols, sym], sym)
  }, [symbols, commit])

  const activate = useCallback((raw: string) => {
    const sym = normalize(raw)
    if (sym && symbols.includes(sym)) commit(symbols, sym)
  }, [symbols, commit])

  /** Close a chip. Closing the ACTIVE one hands the board to its neighbour on
   * the right, or the left when it was last — what closing a browser tab
   * does. Closing the last chip leaves the board with no symbol at all, which
   * is a real state: the tiles fall back to their "pick a symbol" placeholders
   * rather than the strip refusing to empty. */
  const remove = useCallback((raw: string) => {
    const sym = normalize(raw)
    const i = symbols.indexOf(sym)
    if (i < 0) return
    const next = symbols.filter(s => s !== sym)
    commit(next, sym === active ? (next[i] ?? next[i - 1] ?? "") : active)
  }, [symbols, active, commit])

  return { symbols, active, add, activate, remove }
}
