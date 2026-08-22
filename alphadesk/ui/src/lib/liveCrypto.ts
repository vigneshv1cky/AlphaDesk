import { useEffect, useState } from "react"

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

type Conn = {
  es: EventSource
  subs: Set<(ticks: Record<string, CryptoTick>) => void>
  last: Record<string, CryptoTick>
}
let conn: Conn | null = null

function connect(): Conn {
  if (conn) return conn
  const es = new EventSource("/api/stream-crypto")
  const c: Conn = { es, subs: new Set(), last: {} }
  es.onmessage = e => {
    try {
      const payload = JSON.parse((e as MessageEvent).data) as { ticks?: CryptoTick[] }
      if (!payload.ticks?.length) return
      // Replaced, not mutated — subscribers compare identity to decide whether
      // to re-render.
      const next = { ...c.last }
      for (const t of payload.ticks) next[t.symbol] = t
      c.last = next
      c.subs.forEach(fn => fn(next))
    } catch { /* a malformed frame is not fatal */ }
  }
  // EventSource reconnects on its own; nothing to do but let it.
  es.onerror = () => {}
  conn = c
  return c
}

export function useCryptoTicks(): Record<string, CryptoTick> {
  const [ticks, setTicks] = useState<Record<string, CryptoTick>>(() => conn?.last ?? {})
  useEffect(() => {
    const c = connect()
    const fn = (next: Record<string, CryptoTick>) => setTicks(next)
    c.subs.add(fn)
    setTicks(c.last)
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
  return ticks
}
