import type { ChartBar } from "@/lib/api"
import type { Point } from "@/lib/indicators"

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
  | { kind: "histogram"; points: Point[]; color: string; downColor?: string; signs?: boolean }
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

/** Fundamentals. Quarterly points land on a handful of bars across a long
 * range, so `bars` is only used to decide the pane is worth drawing at all. */
export function metricsPane(
  series: { id: string; label: string; points: Point[] }[],
  height: number, style: "bars" | "line" | "area", palette: string[],
): Pane | null {
  const usable = series.filter(s => s.points.length >= 2)
  if (!usable.length) return null
  return {
    id: "metrics",
    height,
    compact: true,
    label: usable.map(s => s.label).join(" · "),
    series: usable.map((s, i) => {
      const color = palette[i % palette.length]
      if (style === "line") return { kind: "line", color, points: s.points, width: 2 } as PaneSeries
      if (style === "area") return { kind: "area", color, points: s.points } as PaneSeries
      return { kind: "histogram", color, points: s.points, signs: true } as PaneSeries
    }),
  }
}

/** The min/max a pane's own axis should span. */
export function paneExtent(pane: Pane): { min: number; max: number } {
  if (pane.range) return pane.range
  let min = Infinity, max = -Infinity
  for (const s of pane.series) {
    for (const p of s.points) {
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
