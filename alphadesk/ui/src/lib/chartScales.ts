/** Scale and tick math for the chart renderer.
 *
 * Pure functions over plain numbers — no DOM, no React. Everything the
 * renderer does visually rests on these four operations (time↔x, price↔y), so
 * they are kept separate to be reasoned about and tested directly rather than
 * inferred from pixels on a screen.
 *
 * The viewport is expressed in BAR INDICES rather than timestamps. Market data
 * is not evenly spaced in time — nights, weekends and holidays are gaps — and
 * a time-linear axis renders those as dead space, which is why no trading
 * chart uses one. Index-linear means every bar gets equal width and the gaps
 * close, which is what a reader expects.
 */

export type Scale = {
  /** Index of the leftmost visible bar; fractional while panning. */
  from: number
  /** Index just past the rightmost visible bar. */
  to: number
  /** Plot area, excluding axes. */
  width: number
  height: number
  min: number
  max: number
}

/** Bar index -> x pixel. Fractional indices are valid mid-pan. */
export function indexToX(s: Scale, i: number): number {
  const span = s.to - s.from
  if (span <= 0) return 0
  return ((i - s.from) / span) * s.width
}

/** x pixel -> bar index. */
export function xToIndex(s: Scale, x: number): number {
  const span = s.to - s.from
  return s.from + (x / s.width) * span
}

/** Price -> y pixel. Inverted, because SVG's y grows downward and price does
 * not. Log mode compresses the axis so equal RATIOS occupy equal space. */
export function priceToY(s: Scale, price: number, log = false): number {
  if (log) {
    const lo = Math.log(Math.max(s.min, 1e-9))
    const hi = Math.log(Math.max(s.max, 1e-9))
    const p = Math.log(Math.max(price, 1e-9))
    return hi === lo ? s.height / 2 : s.height - ((p - lo) / (hi - lo)) * s.height
  }
  const range = s.max - s.min
  // A flat series has no range to scale against; centring beats dividing by
  // zero and beats pinning it to an edge, which would read as a collapse.
  if (range <= 0) return s.height / 2
  return s.height - ((price - s.min) / range) * s.height
}

/** y pixel -> price. */
export function yToPrice(s: Scale, y: number, log = false): number {
  if (log) {
    const lo = Math.log(Math.max(s.min, 1e-9))
    const hi = Math.log(Math.max(s.max, 1e-9))
    return Math.exp(hi - (y / s.height) * (hi - lo))
  }
  const range = s.max - s.min
  return s.max - (y / s.height) * range
}

/** "Nice" numbers for axis ticks — 1, 2, 2.5, 5 and their powers of ten.
 *
 * Ticks land on values a reader recognises. A naive range/8 produces labels
 * like 217.3847, which is precise and useless. */
export function niceStep(rough: number): number {
  if (rough <= 0 || !Number.isFinite(rough)) return 1
  const mag = 10 ** Math.floor(Math.log10(rough))
  const norm = rough / mag
  const step = norm <= 1 ? 1 : norm <= 2 ? 2 : norm <= 2.5 ? 2.5 : norm <= 5 ? 5 : 10
  return step * mag
}

/** Price-axis tick values across the visible range. */
export function priceTicks(min: number, max: number, target = 7): number[] {
  if (!Number.isFinite(min) || !Number.isFinite(max) || max <= min) return []
  const step = niceStep((max - min) / target)
  const first = Math.ceil(min / step) * step
  const out: number[] = []
  // Guard the loop: a pathological step could otherwise run away.
  for (let v = first, n = 0; v <= max && n < 200; v += step, n++) {
    out.push(Number(v.toFixed(10)))
  }
  return out
}

/** How many decimals a price axis needs, from the size of its step.
 * An index at 7,600 wants none; a sub-dollar ticker wants four. */
export function priceDecimals(step: number): number {
  if (step >= 100) return 0
  if (step >= 1) return 2
  if (step >= 0.01) return 2
  return Math.min(6, Math.max(2, Math.ceil(-Math.log10(step)) + 1))
}

/** Pad a min/max so the series never touches the pane edges. */
export function padRange(min: number, max: number, frac = 0.08): { min: number; max: number } {
  if (!Number.isFinite(min) || !Number.isFinite(max)) return { min: 0, max: 1 }
  if (max === min) {
    const bump = Math.abs(max) * 0.01 || 1
    return { min: min - bump, max: max + bump }
  }
  const pad = (max - min) * frac
  return { min: min - pad, max: max + pad }
}

/** Keep a view window overlapping the series.
 *
 * Allows a little empty space on either side — room to draw into the future is
 * worth having — but never lets the bars slide entirely off screen. Panning
 * and zooming share this one rule so they cannot disagree about where the edge
 * of the world is; an unclamped pan strands the reader on blank canvas with no
 * way back except changing the range.
 */
export function clampView(from: number, to: number, total: number): { from: number; to: number } {
  const span = to - from
  let f = from, t = to
  if (t > total + span * 0.3) { t = total + span * 0.3; f = t - span }
  if (f < -span * 0.3) { f = -span * 0.3; t = f + span }
  return { from: f, to: t }
}

/** Zoom about a fixed bar index, so the bar under the cursor stays put — the
 * behaviour every charting tool has, and its absence is immediately obvious. */
export function zoomAt(s: Scale, anchorIndex: number, factor: number, total: number): { from: number; to: number } {
  const span = s.to - s.from
  const next = Math.max(5, Math.min(total * 3, span * factor))
  const ratio = span === 0 ? 0.5 : (anchorIndex - s.from) / span
  const from = anchorIndex - ratio * next
  return clampView(from, from + next, total)
}

/** The min/max of whatever is actually on screen, so the y axis tracks the
 * visible window rather than the whole history. */
export function visibleExtent(
  bars: { h: number; l: number }[], from: number, to: number,
): { min: number; max: number } {
  const lo = Math.max(0, Math.floor(from))
  const hi = Math.min(bars.length - 1, Math.ceil(to))
  let min = Infinity
  let max = -Infinity
  for (let i = lo; i <= hi; i++) {
    const b = bars[i]
    if (!b) continue
    if (b.l < min) min = b.l
    if (b.h > max) max = b.h
  }
  return Number.isFinite(min) ? { min, max } : { min: 0, max: 1 }
}
