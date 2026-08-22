import { useEffect, useState } from "react"

/** Live equity trades for a LIST of symbols, over one shared EventSource.
 *
 * The movers panels poll every minute or two, so in session they flashed once
 * a poll — technically live, and nothing like the ticker beside them. This
 * points them at the equity stream that already existed.
 *
 * WHAT TO EXPECT, because this is Alpaca's IEX tape and not Coinbase: a few
 * percent of consolidated volume. ingest/stream.py measured 23 NVDA trades in
 * 26 seconds, 11 for AAPL, and none at all for ENTA. Busy rows will flash
 * often, quiet ones rarely, some never — and a row that does not flash means
 * this feed saw no print, not that the stock did not trade.
 *
 * Keyed on the symbol LIST: a panel that changes its rows gets a new
 * connection with the new subscription rather than silently watching the old
 * set. The previous one is closed by the last reader leaving, as usual.
 */
export type QuoteTick = { symbol: string; price: number; at: string; stale: boolean }

type Conn = {
  es: EventSource
  subs: Set<(t: Record<string, QuoteTick>) => void>
  last: Record<string, QuoteTick>
}
const conns = new Map<string, Conn>()

function connect(key: string, symbols: string[]): Conn {
  const existing = conns.get(key)
  if (existing) return existing
  const es = new EventSource(`/api/stream-quotes?symbols=${encodeURIComponent(symbols.join(","))}`)
  const c: Conn = { es, subs: new Set(), last: {} }
  es.onmessage = e => {
    try {
      const payload = JSON.parse((e as MessageEvent).data) as { ticks?: QuoteTick[] }
      if (!payload.ticks?.length) return
      const next = { ...c.last }
      for (const t of payload.ticks) next[t.symbol] = t
      c.last = next
      c.subs.forEach(fn => fn(next))
    } catch { /* a malformed frame is not fatal */ }
  }
  es.onerror = () => {}
  conns.set(key, c)
  return c
}

export function useLiveQuotes(symbols: string[]): Record<string, QuoteTick> {
  // Sorted, so two panels watching the same names in a different order share
  // one connection instead of opening two identical ones.
  const key = [...symbols].sort().join(",")
  const [ticks, setTicks] = useState<Record<string, QuoteTick>>({})
  useEffect(() => {
    if (!key) return
    const c = connect(key, key.split(","))
    const fn = (next: Record<string, QuoteTick>) => setTicks(next)
    c.subs.add(fn)
    setTicks(c.last)
    return () => {
      c.subs.delete(fn)
      if (c.subs.size === 0) {
        c.es.close()
        conns.delete(key)
      }
    }
  }, [key])
  return ticks
}
