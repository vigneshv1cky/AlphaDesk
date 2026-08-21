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

/** True range: the greater of today's spread and the two gaps to yesterday's
 * close. The input to ATR, Keltner's width and ADX alike. */
function trueRange(bars: ChartBar[]): number[] {
  return bars.map((b, i) =>
    i === 0 ? b.h - b.l
      : Math.max(b.h - b.l, Math.abs(b.h - bars[i - 1].c), Math.abs(b.l - bars[i - 1].c)))
}

/** Wilder's smoothing: the running average he defined for ATR, ADX and RSI —
 * a 1/period EMA seeded on a simple mean. Distinct from `ema`, whose 2/(n+1)
 * factor makes it turn roughly twice as fast, so the two are NOT
 * interchangeable even though both are "smoothed averages". */
function rma(values: (number | null)[], period: number): (number | null)[] {
  const out: (number | null)[] = new Array(values.length).fill(null)
  let prev: number | null = null
  let seed = 0, seen = 0
  for (let i = 0; i < values.length; i++) {
    const v = values[i]
    if (v == null || !Number.isFinite(v)) continue
    if (prev == null) {
      seed += v
      if (++seen === period) { prev = seed / period; out[i] = prev }
    } else {
      prev = (prev * (period - 1) + v) / period
      out[i] = prev
    }
  }
  return out
}

/** Keltner's width. A SIMPLE mean of true range, not Wilder's — kept as it
 * was so the existing channel does not silently move; the ATR pane below uses
 * Wilder's, which is what "ATR" means when it is the thing being read. */
function atr(bars: ChartBar[], period: number): (number | null)[] {
  const tr = trueRange(bars)
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

/* ── Oscillators ─────────────────────────────────────────────────────────────
 *
 * These draw in their OWN pane rather than over price, because none of them is
 * in price units — %R runs -100..0, OBV is a share count, ATR is a spread.
 *
 * All computed here from the bars already on screen, like the overlays above.
 * RSI and MACD stay server-side because the server is also what measures
 * whether the feed can support them; these inherit that same verdict rather
 * than making their own (see CHART_MIN_COVERAGE) — an oscillator on a sparse
 * feed is precisely the chart that looks right and is not.
 */

/** Highest high and lowest low over the window ending at `i`. */
function extremes(bars: ChartBar[], i: number, period: number): { hi: number; lo: number } {
  let hi = -Infinity, lo = Infinity
  for (let j = i - period + 1; j <= i; j++) {
    if (bars[j].h > hi) hi = bars[j].h
    if (bars[j].l < lo) lo = bars[j].l
  }
  return { hi, lo }
}

const typicalPrice = (b: ChartBar) => (b.h + b.l + b.c) / 3

/** Stochastic oscillator. %K is where the close sits inside the window's
 * range; %D is its 3-period average. A window with no range at all is 50 —
 * dead centre — rather than a divide by zero. */
export function stochastic(bars: ChartBar[], kPeriod = 14, dPeriod = 3): { k: Point[]; d: Point[] } {
  const kRaw: (number | null)[] = new Array(bars.length).fill(null)
  for (let i = kPeriod - 1; i < bars.length; i++) {
    const { hi, lo } = extremes(bars, i, kPeriod)
    kRaw[i] = hi === lo ? 50 : ((bars[i].c - lo) / (hi - lo)) * 100
  }
  const dRaw: (number | null)[] = new Array(bars.length).fill(null)
  for (let i = kPeriod + dPeriod - 2; i < bars.length; i++) {
    let sum = 0
    for (let j = i - dPeriod + 1; j <= i; j++) sum += kRaw[j] ?? 0
    dRaw[i] = sum / dPeriod
  }
  return { k: zip(bars, kRaw), d: zip(bars, dRaw) }
}

/** Williams %R — the stochastic measured from the top of the range, so it runs
 * -100 (at the low) to 0 (at the high). */
export function williamsR(bars: ChartBar[], period = 14): Point[] {
  const out: (number | null)[] = new Array(bars.length).fill(null)
  for (let i = period - 1; i < bars.length; i++) {
    const { hi, lo } = extremes(bars, i, period)
    out[i] = hi === lo ? -50 : ((hi - bars[i].c) / (hi - lo)) * -100
  }
  return zip(bars, out)
}

/** Commodity Channel Index. The 0.015 is Lambert's constant, chosen so most
 * readings land inside ±100; it is not a unit conversion. */
export function cci(bars: ChartBar[], period = 20): Point[] {
  const tp = bars.map(typicalPrice)
  const out: (number | null)[] = new Array(bars.length).fill(null)
  for (let i = period - 1; i < bars.length; i++) {
    let sum = 0
    for (let j = i - period + 1; j <= i; j++) sum += tp[j]
    const mean = sum / period
    let dev = 0
    for (let j = i - period + 1; j <= i; j++) dev += Math.abs(tp[j] - mean)
    const md = dev / period
    out[i] = md === 0 ? 0 : (tp[i] - mean) / (0.015 * md)
  }
  return zip(bars, out)
}

/** Rate of change, as a percentage of the close `period` bars back. */
export function roc(bars: ChartBar[], period = 12): Point[] {
  const out: (number | null)[] = new Array(bars.length).fill(null)
  for (let i = period; i < bars.length; i++) {
    const base = bars[i - period].c
    out[i] = base === 0 ? null : ((bars[i].c - base) / base) * 100
  }
  return zip(bars, out)
}

/** Money Flow Index — RSI weighted by volume, so it needs volume to mean
 * anything. A window with no turnover at all yields nothing rather than the
 * 100 the formula would otherwise report from an empty denominator. */
export function mfi(bars: ChartBar[], period = 14): Point[] {
  const tp = bars.map(typicalPrice)
  const out: (number | null)[] = new Array(bars.length).fill(null)
  for (let i = period; i < bars.length; i++) {
    let pos = 0, neg = 0
    for (let j = i - period + 1; j <= i; j++) {
      const flow = tp[j] * (bars[j].v ?? 0)
      if (tp[j] > tp[j - 1]) pos += flow
      else if (tp[j] < tp[j - 1]) neg += flow
    }
    if (pos + neg === 0) continue
    out[i] = neg === 0 ? 100 : 100 - 100 / (1 + pos / neg)
  }
  return zip(bars, out)
}

/** Average true range, Wilder's smoothing. In price units, so its pane scale
 * is the symbol's own — not comparable across symbols. */
export function atrSeries(bars: ChartBar[], period = 14): Point[] {
  return zip(bars, rma(trueRange(bars), period))
}

/** On-balance volume: a running total that adds the day's volume on an up
 * close and subtracts it on a down one. The LEVEL is arbitrary — only its
 * direction, and whether it agrees with price, carries information. */
export function obv(bars: ChartBar[]): Point[] {
  const out: (number | null)[] = new Array(bars.length).fill(null)
  let acc = 0
  for (let i = 0; i < bars.length; i++) {
    const v = bars[i].v ?? 0
    if (i > 0) {
      if (bars[i].c > bars[i - 1].c) acc += v
      else if (bars[i].c < bars[i - 1].c) acc -= v
    }
    out[i] = acc
  }
  return zip(bars, out)
}

/** ADX with its two directional lines. ADX measures trend STRENGTH only — it
 * says nothing about direction, which is what +DI/-DI are drawn alongside it
 * for. Conventionally read as trending above 25. */
export function adx(bars: ChartBar[], period = 14): { adx: Point[]; plusDI: Point[]; minusDI: Point[] } {
  const n = bars.length
  const plusDM: number[] = new Array(n).fill(0)
  const minusDM: number[] = new Array(n).fill(0)
  for (let i = 1; i < n; i++) {
    const up = bars[i].h - bars[i - 1].h
    const down = bars[i - 1].l - bars[i].l
    // Only the LARGER of the two moves counts, and only if it is outward.
    plusDM[i] = up > down && up > 0 ? up : 0
    minusDM[i] = down > up && down > 0 ? down : 0
  }
  const trS = rma(trueRange(bars), period)
  const pS = rma(plusDM, period)
  const mS = rma(minusDM, period)
  const pdi: (number | null)[] = new Array(n).fill(null)
  const mdi: (number | null)[] = new Array(n).fill(null)
  const dx: (number | null)[] = new Array(n).fill(null)
  for (let i = 0; i < n; i++) {
    const t = trS[i], p = pS[i], m = mS[i]
    if (t == null || p == null || m == null || t === 0) continue
    const P = (100 * p) / t, M = (100 * m) / t
    pdi[i] = P
    mdi[i] = M
    dx[i] = P + M === 0 ? 0 : (100 * Math.abs(P - M)) / (P + M)
  }
  return { adx: zip(bars, rma(dx, period)), plusDI: zip(bars, pdi), minusDI: zip(bars, mdi) }
}

/** The indicators that get their own pane under price. RSI and MACD come from
 * the server; the rest are computed above. */
export type PaneId = "rsi" | "macd" | "stoch" | "williams" | "cci" | "roc" | "mfi" | "atr" | "obv" | "adx"

export const PANE_INDICATORS: { id: PaneId; label: string; group: string }[] = [
  { id: "rsi",      label: "Relative Strength Index (9)",   group: "Momentum" },
  { id: "stoch",    label: "Stochastic Oscillator (14, 3)", group: "Momentum" },
  { id: "williams", label: "Williams %R (14)",              group: "Momentum" },
  { id: "cci",      label: "Commodity Channel Index (20)",  group: "Momentum" },
  { id: "roc",      label: "Rate of Change (12)",           group: "Momentum" },
  { id: "macd",     label: "MACD (12, 26, 9)",              group: "Trend" },
  { id: "adx",      label: "Average Directional Index (14)", group: "Trend" },
  { id: "atr",      label: "Average True Range (14)",       group: "Volatility" },
  { id: "mfi",      label: "Money Flow Index (14)",         group: "Volume" },
  { id: "obv",      label: "On-Balance Volume",             group: "Volume" },
]

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
