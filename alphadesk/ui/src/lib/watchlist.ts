import { useCallback, useEffect, useState } from "react"

/** The watched-symbol list.
 *
 * Browser-local on purpose. AlphaDesk holds no account state and no positions
 * — putting a watchlist in the ledger would make the server the owner of
 * something personal, and the server has nothing to say about it. localStorage
 * keeps it per-browser, which is the honest scope for "symbols I am following".
 *
 * Storage failures are swallowed everywhere: private-mode Safari throws on
 * write, and a terminal that will not render because it could not save a
 * ticker is worse than one that forgets it.
 */

const KEY = "alphadesk.watchlist"

/** Cross-component sync. Two mounted copies of the list must not disagree, and
 * the `storage` event only fires for OTHER tabs, never the one that wrote. */
const listeners = new Set<(symbols: string[]) => void>()

function read(): string[] {
  try {
    const raw = localStorage.getItem(KEY)
    if (!raw) return []
    const parsed: unknown = JSON.parse(raw)
    if (!Array.isArray(parsed)) return []
    return parsed.filter((s): s is string => typeof s === "string")
  } catch {
    return []
  }
}

function write(symbols: string[]): void {
  try {
    localStorage.setItem(KEY, JSON.stringify(symbols))
  } catch {
    /* private mode — the list just does not persist */
  }
  listeners.forEach(fn => fn(symbols))
}

/** Same normalisation the API applies, so a typed symbol and a clicked one are
 * never two different entries. */
export function normalize(raw: string): string {
  return raw
    .toUpperCase()
    .split("")
    .filter(c => /[A-Z0-9.-]/.test(c))
    .join("")
    .slice(0, 12)
}

export function useWatchlist() {
  const [symbols, setSymbols] = useState<string[]>(read)

  useEffect(() => {
    const onLocal = (next: string[]) => setSymbols(next)
    listeners.add(onLocal)
    // Another tab edited the same list.
    const onStorage = (e: StorageEvent) => { if (e.key === KEY) setSymbols(read()) }
    window.addEventListener("storage", onStorage)
    return () => {
      listeners.delete(onLocal)
      window.removeEventListener("storage", onStorage)
    }
  }, [])

  const add = useCallback((raw: string) => {
    const sym = normalize(raw)
    if (!sym) return
    const next = read()
    if (next.includes(sym)) return          // adding twice is a no-op, not a duplicate row
    write([...next, sym])
  }, [])

  const remove = useCallback((raw: string) => {
    const sym = normalize(raw)
    write(read().filter(s => s !== sym))
  }, [])

  const has = useCallback((raw: string) => read().includes(normalize(raw)), [])

  return { symbols, add, remove, has }
}
