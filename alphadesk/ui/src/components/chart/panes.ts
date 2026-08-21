import type { ChartBar } from "@/lib/api"
// Relative with an explicit extension, not the usual `@/` alias: this is a
// VALUE import now, and `pnpm test` runs these files through bare Node, which
// knows nothing about Vite's alias. The type-only import this replaced was
// erased before Node ever tried to resolve it, so the alias cost nothing then
// and breaks the scale-math test now.
import {
  adx, atrSeries, cci, mfi, obv, roc, stochastic, williamsR,
  type PaneId, type Point,
} from "../../lib/indicators.ts"
import { indexToX, type Scale } from "../../lib/chartScales.ts"

/** Pane definitions for the renderer.
 *
 * A pane is a horizontal band under the price with its OWN y scale. That
 * independence is the whole point: volume is in millions of shares, RSI is
 * bounded 0–100, and revenue is in billions. Sharing one axis would flatten
 * every one of them except the largest into the floor.
 *
 * They share the x scale, because they are all describing the same bars — a
 * pane that scrolled independently of the price above it would be lying about
 * which bar a value belongs to.
 */

export type PaneSeries =
  | {
      kind: "histogram"; points: Point[]; color: string; downColor?: string; signs?: boolean
      /** Sum into wider columns when the bars get denser than the pixels.
       * Opt-in because it is only meaningful for a QUANTITY: summing a bucket
       * of volume is still volume, whereas summing a bucket of MACD histogram
       * values is not any reading at all. */
      aggregate?: boolean
    }
  | { kind: "line"; points: Point[]; color: string; width?: number }
  | { kind: "area"; points: Point[]; color: string }

export type Pane = {
  id: string
  height: number
  series: PaneSeries[]
  /** Fixed scale, for a bounded indicator like RSI. Omitted means fit to data. */
  range?: { min: number; max: number }
  /** Horizontal reference lines — RSI's 30/70, MACD's zero. */
  levels?: number[]
  /** Drawn top-left inside the pane, so a stack of them stays legible. */
  label?: string
  /** How to format this pane's axis. Volume and revenue want compact
   * notation; an oscillator wants plain numbers. */
  compact?: boolean
}

const fmtCompact = new Intl.NumberFormat("en-US", { notation: "compact", maximumFractionDigits: 1 })

export function paneAxisLabel(v: number, compact?: boolean): string {
  if (compact) return fmtCompact.format(v)
  return Math.abs(v) >= 100 ? v.toFixed(0) : v.toFixed(2)
}

/** The volume band. Coloured by each bar's own direction, because volume alone
 * says nothing about which way the move went. */
export function volumePane(bars: ChartBar[], height: number, gain: string, loss: string): Pane | null {
  if (!bars.some(b => b.v)) return null
  return {
    id: "volume",
    height,
    compact: true,
    series: [{
      kind: "histogram",
      points: bars.map(b => ({ t: b.t, v: b.v ?? 0 })),
      color: gain,
      downColor: loss,
      // Direction comes from the bar, not the value's sign.
      signs: false,
      // A session of minute bars is thousands of one-pixel columns — a solid
      // block rather than a histogram. Summed buckets are still volume.
      aggregate: true,
    }],
  }
}

/** RSI, with its conventional bands. The 0–100 range is FIXED rather than
 * fitted: an RSI pane auto-scaled to its own data would put 45 at the top of
 * the pane and make a neutral reading look extreme. */
export function rsiPane(bars: ChartBar[], values: (number | null)[], height: number,
                        color: string, thresholds: { oversold: number; overbought: number }): Pane | null {
  const points = bars
    .map((b, i) => ({ t: b.t, v: values[i] }))
    .filter((p): p is Point => p.v != null && Number.isFinite(p.v))
  if (points.length < 2) return null
  return {
    id: "rsi",
    height,
    label: "RSI 9",
    range: { min: 0, max: 100 },
    levels: [thresholds.oversold, 50, thresholds.overbought],
    series: [{ kind: "line", points, color, width: 1.5 }],
  }
}

/** MACD: the histogram of the divergence, plus both lines over it. */
export function macdPane(
  bars: ChartBar[],
  macd: (number | null)[], signal: (number | null)[], hist: (number | null)[],
  height: number, line: string, signalColor: string, gain: string, loss: string,
): Pane | null {
  const pick = (vals: (number | null)[]) => bars
    .map((b, i) => ({ t: b.t, v: vals[i] }))
    .filter((p): p is Point => p.v != null && Number.isFinite(p.v))
  const h = pick(hist)
  if (h.length < 2) return null
  return {
    id: "macd",
    height,
    label: "MACD 12,26,9",
    levels: [0],
    series: [
      // signs:true — here the VALUE's sign is the direction, unlike volume.
      { kind: "histogram", points: h, color: gain, downColor: loss, signs: true },
      { kind: "line", points: pick(macd), color: line, width: 1.5 },
      { kind: "line", points: pick(signal), color: signalColor, width: 1.5 },
    ],
  }
}

/** The min/max a pane's own axis should span.
 *
 * `visible` limits it to the bars on screen, which is what the price pane has
 * always done and what these did not. Scaling a pane to the WHOLE series meant
 * one spike anywhere in it set the scale forever: on a 5-session NVDA window
 * a single -1.35 MACD print against a typical +-0.1 left the middle 90% of
 * every series inside a quarter of the pane, and zooming into a calm stretch
 * did not recover the detail, because the outlier was still counted while
 * being nowhere on screen. Omitted, the whole series is measured, which is
 * still right for a caller that draws all of it.
 */
export function paneExtent(
  pane: Pane, visible?: (t: string) => boolean,
): { min: number; max: number } {
  if (pane.range) return pane.range
  let min = Infinity, max = -Infinity
  for (const s of pane.series) {
    for (const p of s.points) {
      if (visible && !visible(p.t)) continue
      if (p.v < min) min = p.v
      if (p.v > max) max = p.v
    }
  }
  if (!Number.isFinite(min)) return { min: 0, max: 1 }
  // A histogram reads from zero — starting its axis at the smallest bar makes
  // every bar look the same height.
  const histogram = pane.series.some(s => s.kind === "histogram")
  if (histogram) {
    min = Math.min(0, min)
    max = Math.max(0, max)
  }
  if (min === max) return { min: min - 1, max: max + 1 }
  const pad = (max - min) * 0.08
  // Pad away from zero only. Padding a histogram's floor below zero puts a
  // negative tick on an axis that cannot go negative — a volume pane reading
  // "-123M" is simply wrong, and it was.
  return {
    min: histogram && min === 0 ? 0 : min - pad,
    max: histogram && max === 0 ? 0 : max + pad,
  }
}

/** The panes computed in the browser rather than served.
 *
 * One builder rather than eight exports: every one of these is the same shape
 * — a series or two, a scale, and the conventional reference lines — and the
 * differences between them are data, not control flow the caller should have
 * to know. Returns null when the series is too short to draw, which the caller
 * filters out; a pane with one point in it is a horizontal line that means
 * nothing.
 *
 * Bounded oscillators declare a FIXED range for the same reason RSI does:
 * fitted to its own data, a %R sitting quietly at -55 all session would be
 * drawn touching both edges of the pane and read as violent.
 */
export function oscillatorPane(
  id: Exclude<PaneId, "rsi" | "macd">,
  bars: ChartBar[],
  height: number,
  theme: { gain: string; loss: string },
): Pane | null {
  const base = { id, height }
  const enough = (pts: Point[]) => pts.length >= 2

  switch (id) {
    case "stoch": {
      const { k, d } = stochastic(bars, 14, 3)
      if (!enough(k)) return null
      return {
        ...base, label: "Stoch 14,3", range: { min: 0, max: 100 }, levels: [20, 50, 80],
        series: [
          { kind: "line", points: k, color: "#4c8dff", width: 1.5 },
          { kind: "line", points: d, color: "#f5a524", width: 1.5 },
        ],
      }
    }
    case "williams": {
      const pts = williamsR(bars, 14)
      if (!enough(pts)) return null
      return {
        ...base, label: "%R 14", range: { min: -100, max: 0 }, levels: [-80, -50, -20],
        series: [{ kind: "line", points: pts, color: "#9353d3", width: 1.5 }],
      }
    }
    case "cci": {
      const pts = cci(bars, 20)
      if (!enough(pts)) return null
      // Unbounded in principle, so fitted — but the ±100 rails still drawn,
      // because they are what the reading is conventionally judged against.
      return {
        ...base, label: "CCI 20", levels: [-100, 0, 100],
        series: [{ kind: "line", points: pts, color: "#5ec6d6", width: 1.5 }],
      }
    }
    case "roc": {
      const pts = roc(bars, 12)
      if (!enough(pts)) return null
      return {
        ...base, label: "ROC 12", levels: [0],
        series: [{ kind: "line", points: pts, color: "#17c964", width: 1.5 }],
      }
    }
    case "mfi": {
      const pts = mfi(bars, 14)
      if (!enough(pts)) return null       // no volume on this feed → no pane
      return {
        ...base, label: "MFI 14", range: { min: 0, max: 100 }, levels: [20, 50, 80],
        series: [{ kind: "line", points: pts, color: "#d6a35e", width: 1.5 }],
      }
    }
    case "atr": {
      const pts = atrSeries(bars, 14)
      if (!enough(pts)) return null
      // Price units, so it gets the plain axis rather than compact notation.
      return {
        ...base, label: "ATR 14",
        series: [{ kind: "line", points: pts, color: "#f31260", width: 1.5 }],
      }
    }
    case "obv": {
      const pts = obv(bars)
      if (!enough(pts)) return null
      // Share counts run to the millions, and the LEVEL carries no meaning —
      // only the slope — so compact notation loses nothing worth keeping.
      return {
        ...base, label: "OBV", compact: true, levels: [0],
        series: [{ kind: "area", points: pts, color: "#7ea6f0" }],
      }
    }
    case "adx": {
      const { adx: a, plusDI, minusDI } = adx(bars, 14)
      if (!enough(a)) return null
      // 25 is the conventional trending/not line. ADX alone says nothing about
      // WHICH way, which is why both directional lines are drawn with it.
      return {
        ...base, label: "ADX 14", range: { min: 0, max: 100 }, levels: [25],
        series: [
          { kind: "line", points: plusDI, color: theme.gain, width: 1 },
          { kind: "line", points: minusDI, color: theme.loss, width: 1 },
          { kind: "line", points: a, color: "#8f9bb3", width: 1.5 },
        ],
      }
    }
  }
}

/** Group a histogram's bars into columns wide enough to read.
 *
 * At intraday density there are several bars per pixel, and drawing one 1px
 * column each produces a solid block that reads as a filled area rather than a
 * histogram — which is the single biggest reason our volume band did not look
 * like theirs. Below the threshold nothing is grouped and the bars sit on their
 * own index, exactly as before.
 *
 * A column's direction is where the MAJORITY of its volume came from, not its
 * first or last bar — one big print should not colour a quiet hour.
 *
 * Module scope, not a closure inside the component: it feeds a useMemo, and a
 * function rebuilt every render either has to be left out of the deps (a lie)
 * or put in (defeating the memo).
 */
export function volumeColumns(
entries: { i: number; v: number; up: boolean }[],
s: Scale, plotW: number, minPx: number,
): { x: number; w: number; v: number; up: boolean }[] {
if (!entries.length) return []
const natural = plotW / entries.length
if (natural >= minPx) {
  return entries.map(e => ({
    x: indexToX(s, e.i + 0.5), w: Math.max(1, natural * 0.7), v: e.v, up: e.up,
  }))
}
const per = Math.ceil(entries.length / Math.max(1, Math.floor(plotW / minPx)))
const out: { x: number; w: number; v: number; up: boolean }[] = []
for (let k = 0; k < entries.length; k += per) {
  let sum = 0, upVol = 0
  const first = entries[k].i
  let lastIdx = first
  for (let j = k; j < Math.min(k + per, entries.length); j++) {
    sum += entries[j].v
    if (entries[j].up) upVol += entries[j].v
    lastIdx = entries[j].i
  }
  const x0 = indexToX(s, first), x1 = indexToX(s, lastIdx + 1)
  out.push({ x: (x0 + x1) / 2, w: Math.max(1, (x1 - x0) * 0.72), v: sum, up: upVol * 2 >= sum })
}
return out
}

/** Which trading session each bar belongs to, and where the days break.
 *
 * An intraday chart spanning several days is drawn as one continuous line,
 * because the x scale is bar INDEX — so the overnight gap between a 16:00
 * close and the next 09:30 open occupies exactly one bar's width, the same as
 * a single minute. Nothing on the canvas says a night passed there. Marking
 * the breaks is what lets a reader tell a gap-down from a move.
 *
 * Sessions are classified in EASTERN time via Intl, not by a fixed UTC offset:
 * the market keeps its own clock, and a hardcoded -5 would mislabel every bar
 * for the eight months of the year the US is on daylight time.
 *
 * Extended hours are shaded where present. On the IEX feed that is rare —
 * measured on a 5-session NVDA window, 1,559 regular bars against 8 pre-market
 * and no after-hours at all — so this will usually draw nothing. That is the
 * honest outcome: a band appears when the feed actually printed outside the
 * session, rather than being implied whenever the clock says it could have.
 */
export type SessionKind = "pre" | "regular" | "after"

export type SessionLayout = {
  /** Runs of consecutive bars in one session kind, as [start, end] indices. */
  bands: { kind: SessionKind; from: number; to: number }[]
  /** Bar indices where the ET calendar date changes — a new trading day. */
  dividers: number[]
}

const OPEN_MIN = 9 * 60 + 30
const CLOSE_MIN = 16 * 60

export function sessionLayout(bars: { t: string }[]): SessionLayout {
  const bands: SessionLayout["bands"] = []
  const dividers: number[] = []
  if (!bars.length) return { bands, dividers }

  // One formatter, reused. Constructing one per bar is what makes date
  // handling show up in a profile at a few thousand bars.
  const fmt = new Intl.DateTimeFormat("en-US", {
    timeZone: "America/New_York",
    hour12: false, year: "numeric", month: "2-digit", day: "2-digit",
    hour: "2-digit", minute: "2-digit",
  })

  let day = ""
  let runKind: SessionKind | null = null
  let runFrom = 0

  for (let i = 0; i < bars.length; i++) {
    const p = fmt.formatToParts(new Date(bars[i].t))
    const get = (t: string) => p.find(x => x.type === t)?.value ?? "0"
    const d = `${get("year")}-${get("month")}-${get("day")}`
    // Intl renders midnight as hour 24 under hour12:false; normalise it, or
    // the first bar of a day classifies as after-hours.
    const minute = (Number(get("hour")) % 24) * 60 + Number(get("minute"))
    const kind: SessionKind =
      minute < OPEN_MIN ? "pre" : minute < CLOSE_MIN ? "regular" : "after"

    if (d !== day) {
      if (day) dividers.push(i)
      day = d
    }
    if (kind !== runKind) {
      if (runKind && runKind !== "regular") bands.push({ kind: runKind, from: runFrom, to: i })
      runKind = kind
      runFrom = i
    }
  }
  if (runKind && runKind !== "regular") bands.push({ kind: runKind, from: runFrom, to: bars.length })
  return { bands, dividers }
}
