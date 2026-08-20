import type { ChartBar } from "@/lib/api"

/** Overlay indicator math, computed in the browser from the bars already on
 * screen.
 *
 * Client-side on purpose. RSI and MACD are computed on the server because the
 * server also decides whether the feed is dense enough to trust them — that
 * gate is a product rule, not a display detail. A moving average carries no
 * such claim: it is a restatement of the closes you can already see, so
 * computing it here costs one pass over an array the page is holding anyway
 * and adds no round trip when you toggle it.
 *
 * Every function returns an array the same length as its input, with `null`
 * wherever the window has not filled yet, so a caller can zip it against the
 * bars by index without tracking an offset.
 */

export type Point = { t: string; v: number }

function zip(bars: ChartBar[], values: (number | null)[]): Point[] {
  const out: Point[] = []
  for (let i = 0; i < bars.length; i++) {
    const v = values[i]
    if (v != null && Number.isFinite(v)) out.push({ t: bars[i].t, v })
  }
  return out
}

export function sma(bars: ChartBar[], period: number): Point[] {
  const out: (number | null)[] = new Array(bars.length).fill(null)
  let sum = 0
  for (let i = 0; i < bars.length; i++) {
    sum += bars[i].c
    if (i >= period) sum -= bars[i - period].c
    if (i >= period - 1) out[i] = sum / period
  }
  return zip(bars, out)
}

export function ema(bars: ChartBar[], period: number): Point[] {
  const out: (number | null)[] = new Array(bars.length).fill(null)
  const k = 2 / (period + 1)
  let prev: number | null = null
  for (let i = 0; i < bars.length; i++) {
    // Seeded with a simple average of the first `period` closes rather than
    // the first close alone — seeding on one print lets a single outlier bias
    // the whole series, and on a sparse feed that first print is exactly the
    // one most likely to be unrepresentative.
    if (i === period - 1) {
      let s = 0
      for (let j = 0; j < period; j++) s += bars[j].c
      prev = s / period
      out[i] = prev
    } else if (prev != null) {
      prev = bars[i].c * k + prev * (1 - k)
      out[i] = prev
    }
  }
  return zip(bars, out)
}

/** Bollinger bands — the middle SMA plus/minus `mult` standard deviations.
 * Population deviation over the window, which is what the standard defines. */
export function bollinger(bars: ChartBar[], period = 20, mult = 2): {
  upper: Point[]; middle: Point[]; lower: Point[]
} {
  const up: (number | null)[] = new Array(bars.length).fill(null)
  const mid: (number | null)[] = new Array(bars.length).fill(null)
  const low: (number | null)[] = new Array(bars.length).fill(null)
  for (let i = period - 1; i < bars.length; i++) {
    let sum = 0
    for (let j = i - period + 1; j <= i; j++) sum += bars[j].c
    const mean = sum / period
    let variance = 0
    for (let j = i - period + 1; j <= i; j++) variance += (bars[j].c - mean) ** 2
    const sd = Math.sqrt(variance / period)
    mid[i] = mean
    up[i] = mean + mult * sd
    low[i] = mean - mult * sd
  }
  return { upper: zip(bars, up), middle: zip(bars, mid), lower: zip(bars, low) }
}

/** Volume-weighted average price, reset at each session boundary.
 *
 * The reset matters: VWAP is a session statistic, and carrying it across an
 * overnight gap produces a line that means nothing on either day. Bars carry
 * an ISO timestamp, so the date part is the session key.
 */
export function vwap(bars: ChartBar[]): Point[] {
  const out: (number | null)[] = new Array(bars.length).fill(null)
  let day = ""
  let pv = 0
  let vol = 0
  for (let i = 0; i < bars.length; i++) {
    const b = bars[i]
    const d = b.t.slice(0, 10)
    if (d !== day) { day = d; pv = 0; vol = 0 }
    const typical = (b.h + b.l + b.c) / 3
    const v = b.v ?? 0
    pv += typical * v
    vol += v
    out[i] = vol > 0 ? pv / vol : null
  }
  return zip(bars, out)
}

/** Weighted moving average — linearly weighted, heaviest on the newest close.
 * Between an SMA and an EMA in how fast it turns. */
export function wma(bars: ChartBar[], period: number): Point[] {
  const out: (number | null)[] = new Array(bars.length).fill(null)
  const denom = (period * (period + 1)) / 2
  for (let i = period - 1; i < bars.length; i++) {
    let acc = 0
    for (let k = 0; k < period; k++) acc += bars[i - period + 1 + k].c * (k + 1)
    out[i] = acc / denom
  }
  return zip(bars, out)
}

/** Take a Point series back to a bar-shaped array so an EMA can be run over
 * it. DEMA and TEMA are EMAs of EMAs, and this is the join between them. */
function asBars(bars: ChartBar[], pts: Point[]): ChartBar[] {
  const by = new Map(pts.map(p => [p.t, p.v]))
  return bars.filter(b => by.has(b.t)).map(b => ({ ...b, c: by.get(b.t)! }))
}

/** Double EMA: 2·EMA − EMA(EMA). Cuts the lag an EMA carries. */
export function dema(bars: ChartBar[], period: number): Point[] {
  const e1 = ema(bars, period)
  const e2 = ema(asBars(bars, e1), period)
  const by1 = new Map(e1.map(p => [p.t, p.v]))
  return e2.map(p => ({ t: p.t, v: 2 * (by1.get(p.t) ?? p.v) - p.v }))
}

/** Triple EMA: 3·EMA − 3·EMA² + EMA³. Less lag again, more noise with it. */
export function tema(bars: ChartBar[], period: number): Point[] {
  const e1 = ema(bars, period)
  const e2 = ema(asBars(bars, e1), period)
  const e3 = ema(asBars(bars, e2), period)
  const by1 = new Map(e1.map(p => [p.t, p.v]))
  const by2 = new Map(e2.map(p => [p.t, p.v]))
  return e3.map(p => ({
    t: p.t,
    v: 3 * (by1.get(p.t) ?? p.v) - 3 * (by2.get(p.t) ?? p.v) + p.v,
  }))
}

/** True range, the input to Keltner's width. */
function atr(bars: ChartBar[], period: number): (number | null)[] {
  const tr: number[] = bars.map((b, i) =>
    i === 0 ? b.h - b.l
      : Math.max(b.h - b.l, Math.abs(b.h - bars[i - 1].c), Math.abs(b.l - bars[i - 1].c)))
  const out: (number | null)[] = new Array(bars.length).fill(null)
  let sum = 0
  for (let i = 0; i < bars.length; i++) {
    sum += tr[i]
    if (i >= period) sum -= tr[i - period]
    if (i >= period - 1) out[i] = sum / period
  }
  return out
}

/** Keltner channels — an EMA centre with ATR-scaled rails. Unlike Bollinger,
 * the width tracks true range rather than closing deviation, so a gap widens
 * it where Bollinger would shrug. */
export function keltner(bars: ChartBar[], period = 20, mult = 2): {
  upper: Point[]; middle: Point[]; lower: Point[]
} {
  const mid = ema(bars, period)
  const a = atr(bars, period)
  const byIndex = new Map(bars.map((b, i) => [b.t, i]))
  const up: Point[] = [], low: Point[] = []
  for (const p of mid) {
    const i = byIndex.get(p.t)!
    const width = a[i]
    if (width == null) continue
    up.push({ t: p.t, v: p.v + mult * width })
    low.push({ t: p.t, v: p.v - mult * width })
  }
  return { upper: up, middle: mid, lower: low }
}

/** Donchian price channel — the highest high and lowest low of the window. */
export function priceChannel(bars: ChartBar[], period = 20): {
  upper: Point[]; lower: Point[]
} {
  const up: (number | null)[] = new Array(bars.length).fill(null)
  const low: (number | null)[] = new Array(bars.length).fill(null)
  for (let i = period - 1; i < bars.length; i++) {
    let hi = -Infinity, lo = Infinity
    for (let j = i - period + 1; j <= i; j++) {
      hi = Math.max(hi, bars[j].h)
      lo = Math.min(lo, bars[j].l)
    }
    up[i] = hi; low[i] = lo
  }
  return { upper: zip(bars, up), lower: zip(bars, low) }
}

/** Price envelopes — an SMA shifted by a fixed percentage either way. */
export function envelopes(bars: ChartBar[], period = 20, pct = 2.5): {
  upper: Point[]; middle: Point[]; lower: Point[]
} {
  const mid = sma(bars, period)
  const k = pct / 100
  return {
    middle: mid,
    upper: mid.map(p => ({ t: p.t, v: p.v * (1 + k) })),
    lower: mid.map(p => ({ t: p.t, v: p.v * (1 - k) })),
  }
}

export type OverlayId =
  | "sma20" | "sma50" | "ema20" | "ema50" | "wma20" | "dema20" | "tema20"
  | "bb" | "keltner" | "channel" | "envelopes" | "vwap"

/** Grouped the way theirs groups them, so the menu can be scanned by kind
 * rather than read end to end. */
export const OVERLAYS: { id: OverlayId; label: string; color: string; group: string }[] = [
  { id: "sma20",     label: "Simple Moving Average (20)",            color: "#f5a524", group: "Moving averages" },
  { id: "sma50",     label: "Simple Moving Average (50)",            color: "#f7b955", group: "Moving averages" },
  { id: "ema20",     label: "Exponential Moving Average (20)",       color: "#17c964", group: "Moving averages" },
  { id: "ema50",     label: "Exponential Moving Average (50)",       color: "#45d483", group: "Moving averages" },
  { id: "wma20",     label: "Weighted Moving Average (20)",          color: "#9353d3", group: "Moving averages" },
  { id: "dema20",    label: "Double Exponential Moving Average (20)", color: "#b07ade", group: "Moving averages" },
  { id: "tema20",    label: "Triple Exponential Moving Average (20)", color: "#c99ce8", group: "Moving averages" },
  { id: "bb",        label: "Bollinger Bands (20, 2)",               color: "#7ea6f0", group: "Bands & channels" },
  { id: "keltner",   label: "Keltner Channels (20, 2)",              color: "#5ec6d6", group: "Bands & channels" },
  { id: "channel",   label: "Price Channel (20)",                    color: "#8f9bb3", group: "Bands & channels" },
  { id: "envelopes", label: "Price Envelopes (20, 2.5%)",            color: "#d6a35e", group: "Bands & channels" },
  { id: "vwap",      label: "VWAP",                                  color: "#f31260", group: "Volume" },
]


/** Turn the chosen overlay ids into series the renderer can draw.
 *
 * The band indicators expand to several lines, so this returns a flat list
 * rather than one entry per id — the renderer should not have to know which
 * indicators happen to be multi-line.
 */
export function buildOverlays(
  bars: ChartBar[], ids: OverlayId[],
): { color: string; points: Point[]; width?: number }[] {
  const out: { color: string; points: Point[]; width?: number }[] = []
  const colourOf = (id: OverlayId) => OVERLAYS.find(o => o.id === id)!.color
  const fade = (hex: string, a: number) => {
    const n = hex.replace("#", "")
    const v = parseInt(n.length === 3 ? n.split("").map(c => c + c).join("") : n, 16)
    return `rgba(${(v >> 16) & 255}, ${(v >> 8) & 255}, ${v & 255}, ${a})`
  }
  for (const id of ids) {
    const c = colourOf(id)
    if (id === "sma20") out.push({ color: c, points: sma(bars, 20) })
    else if (id === "sma50") out.push({ color: c, points: sma(bars, 50) })
    else if (id === "ema20") out.push({ color: c, points: ema(bars, 20) })
    else if (id === "ema50") out.push({ color: c, points: ema(bars, 50) })
    else if (id === "wma20") out.push({ color: c, points: wma(bars, 20) })
    else if (id === "dema20") out.push({ color: c, points: dema(bars, 20) })
    else if (id === "tema20") out.push({ color: c, points: tema(bars, 20) })
    else if (id === "vwap") out.push({ color: c, points: vwap(bars) })
    else if (id === "bb") {
      const b = bollinger(bars, 20, 2)
      out.push({ color: fade(c, 0.75), points: b.upper },
               { color: fade(c, 0.45), points: b.middle },
               { color: fade(c, 0.75), points: b.lower })
    } else if (id === "keltner") {
      const k = keltner(bars, 20, 2)
      out.push({ color: fade(c, 0.75), points: k.upper },
               { color: fade(c, 0.45), points: k.middle },
               { color: fade(c, 0.75), points: k.lower })
    } else if (id === "envelopes") {
      const e = envelopes(bars, 20, 2.5)
      out.push({ color: fade(c, 0.75), points: e.upper },
               { color: fade(c, 0.45), points: e.middle },
               { color: fade(c, 0.75), points: e.lower })
    } else if (id === "channel") {
      const ch = priceChannel(bars, 20)
      out.push({ color: fade(c, 0.75), points: ch.upper },
               { color: fade(c, 0.75), points: ch.lower })
    }
  }
  return out
}
