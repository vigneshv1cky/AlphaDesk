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

export type OverlayId = "sma20" | "sma50" | "ema20" | "bb" | "vwap"

export const OVERLAYS: { id: OverlayId; label: string; color: string }[] = [
  { id: "sma20", label: "SMA 20", color: "#f5a524" },
  { id: "sma50", label: "SMA 50", color: "#9353d3" },
  { id: "ema20", label: "EMA 20", color: "#17c964" },
  { id: "bb", label: "Bollinger 20,2", color: "#7ea6f0" },
  { id: "vwap", label: "VWAP", color: "#f31260" },
]
