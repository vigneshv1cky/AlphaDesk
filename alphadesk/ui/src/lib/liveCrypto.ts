import { useEffect, useState } from "react"
import type { MoverRow } from "@/lib/api"

/** Live crypto prices for the ticker, over one shared EventSource.
 *
 * ONE connection for every product and every mounted reader, unlike lib/live.ts
 * which opens one per symbol. The tape shows the same handful of products to
 * everyone, so a connection per product would carry identical traffic several
 * times over.
 *
 * The stream owns nothing but the price. /api/tape still owns the label and the
 * day's change — the same split the chart uses, where the polled series owns
 * the bars and a tick only moves the live edge.
 */
export type CryptoTick = { symbol: string; price: number; at: string; stale: boolean }

/** The movers board, pushed on the same connection at its own slower cadence —
 * twenty rows that get re-ranked, not one number. Shape matches /api/crypto so
 * the panel does not care which produced it. */
export type CryptoBoard = {
  all: MoverRow[]; most_active: MoverRow[]; gainers: MoverRow[]; losers: MoverRow[]
}

type State = { ticks: Record<string, CryptoTick>; board: CryptoBoard | null }

type Conn = {
  es: EventSource
  subs: Set<(s: State) => void>
  last: State
}
let conn: Conn | null = null

function connect(): Conn {
  if (conn) return conn
  const es = new EventSource("/api/stream-crypto")
  const c: Conn = { es, subs: new Set(), last: { ticks: {}, board: null } }
  es.onmessage = e => {
    try {
      const payload = JSON.parse((e as MessageEvent).data) as
        { ticks?: CryptoTick[]; board?: CryptoBoard }
      if (!payload.ticks?.length && !payload.board) return
      // Replaced, not mutated — subscribers compare identity to decide whether
      // to re-render.
      const ticks = payload.ticks?.length ? { ...c.last.ticks } : c.last.ticks
      if (payload.ticks?.length) for (const t of payload.ticks) ticks[t.symbol] = t
      c.last = { ticks, board: payload.board ?? c.last.board }
      c.subs.forEach(fn => fn(c.last))
    } catch { /* a malformed frame is not fatal */ }
  }
  // EventSource reconnects on its own; nothing to do but let it.
  es.onerror = () => {}
  conn = c
  return c
}

function useCryptoStream(): State {
  const [state, setState] = useState<State>(() => conn?.last ?? { ticks: {}, board: null })
  useEffect(() => {
    const c = connect()
    const fn = (next: State) => setState(next)
    c.subs.add(fn)
    setState(c.last)
    return () => {
      c.subs.delete(fn)
      // Last reader out closes it, so a terminal with no tape open holds no
      // socket — the same rule the server applies to its upstream.
      if (c.subs.size === 0) {
        c.es.close()
        if (conn === c) conn = null
      }
    }
  }, [])
  return state
}

export function useCryptoTicks(): Record<string, CryptoTick> {
  return useCryptoStream().ticks
}

/** Null until the first board frame lands, so the caller keeps whatever it
 * already had rather than blanking the panel on mount. */
export function useCryptoBoard(): CryptoBoard | null {
  return useCryptoStream().board
}
