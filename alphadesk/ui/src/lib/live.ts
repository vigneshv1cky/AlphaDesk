import { useEffect, useState } from "react"

/** The live trade feed for one symbol, over Server-Sent Events.
 *
 * EventSource rather than a websocket: the browser has nothing to say back, so
 * the second protocol would buy nothing, and this brings reconnection and
 * backoff with it instead of us writing them.
 *
 * ONE connection per symbol, shared — the same rule lib/queries applies to the
 * REST endpoints, and for a sharper reason here. Each hook opening its own
 * EventSource meant the chart and the quote panel held two streams of the same
 * ticks, two server tasks and two upstream references for one board; over
 * HTTP/1.1 a browser will only keep about six connections to an origin, so a
 * few panels would have started starving the polling requests.
 *
 * The stream is an OVERLAY on the polled series, never a replacement for it.
 * REST still owns the bars — their structure, their history, the coverage
 * verdict — and a tick only ever moves the live edge between polls. That split
 * matters because the feed is IEX: it prints a fraction of consolidated
 * volume, so a symbol can be genuinely quiet here for minutes while trading
 * perfectly well elsewhere. Anything shown from a tick has to be able to say
 * how old it is; `stale` marks when the server last heard one.
 */
export type LiveTick = {
  symbol: string
  price: number
  size: number
  /** Exchange timestamp of the trade. */
  at: string
  /** Seconds since the SERVER received it, so a wedged connection shows up. */
  age_s: number
  stale: boolean
}

type Sub = { onTick: (t: LiveTick) => void; onLive: (live: boolean) => void }

type Conn = {
  es: EventSource
  subs: Set<Sub>
  last: LiveTick | null
  live: boolean
}

const conns = new Map<string, Conn>()

function acquire(symbol: string, sub: Sub): void {
  let c = conns.get(symbol)
  if (!c) {
    const es = new EventSource(`/api/stream/${encodeURIComponent(symbol)}`)
    c = { es, subs: new Set(), last: null, live: false }
    conns.set(symbol, c)
    // The server says up front whether it could subscribe at all, so "no keys"
    // and "quiet stock" are distinguishable rather than both being silence.
    es.addEventListener("hello", e => {
      try {
        const live = !!JSON.parse((e as MessageEvent).data).live
        c!.live = live
        c!.subs.forEach(s => s.onLive(live))
      } catch { /* ignore */ }
    })
    es.onmessage = e => {
      try {
        const t = JSON.parse(e.data) as LiveTick
        if (!t || typeof t.price !== "number") return
        c!.last = t
        c!.subs.forEach(s => s.onTick(t))
      } catch { /* a malformed frame is not worth tearing the stream down for */ }
    }
    // No onerror handler on purpose: EventSource reconnects by itself, and
    // treating a dropped connection as an error state would flash a warning
    // every time a laptop lid closes.
  }
  c.subs.add(sub)
  // A late joiner should not have to wait for the next print to see a price.
  if (c.last) sub.onTick(c.last)
  if (c.live) sub.onLive(true)
}

function release(symbol: string, sub: Sub): void {
  const c = conns.get(symbol)
  if (!c) return
  c.subs.delete(sub)
  if (c.subs.size) return
  c.es.close()
  conns.delete(symbol)
}

export function useLiveTrade(symbol: string): { tick: LiveTick | null; live: boolean } {
  const [tick, setTick] = useState<LiveTick | null>(null)
  const [live, setLive] = useState(false)

  useEffect(() => {
    setTick(null)
    setLive(false)
    if (!symbol) return
    const sub: Sub = { onTick: setTick, onLive: setLive }
    acquire(symbol, sub)
    return () => release(symbol, sub)
  }, [symbol])

  return { tick, live }
}
