import { useCallback, useEffect, useId, useMemo, useRef, useState } from "react"
import type { ChartBar } from "@/lib/api"
import {
  clampView, indexToX, padRange, priceDecimals, priceTicks, priceToY,
  visibleExtent, xToIndex, yToPrice, zoomAt, type Scale,
} from "@/lib/chartScales"
import { paneAxisLabel, paneExtent, sessionLayout, volumeColumns, type Pane, type PaneSeries } from "@/components/chart/panes"

/** The chart renderer.
 *
 * Ours rather than a library's, and SVG rather than canvas, which is the same
 * choice AlphaSpace made. The reason to own it is control: every axis label,
 * every tick, the exact crosshair behaviour and the pane layout answer to this
 * file instead of to another project's options object.
 *
 * PERFORMANCE — the thing that makes hand-rolled SVG charts fall over is one
 * element per bar. At 6,937 daily bars that is 14,000 nodes and every pan
 * re-lays-out the document. So candles are drawn as FOUR paths (up bodies, up
 * wicks, down bodies, down wicks) built as strings, and volume as two. Node
 * count is then constant no matter how much history is loaded.
 *
 * PROJECTION — `timeToCoordinate` and `priceToCoordinate` are exposed with the
 * same shape the drawing layer already consumed from the previous library, so
 * the ten drawing tools work against this renderer untouched.
 */

export type SeriesKind = "candles" | "line" | "area" | "bars"
export type ScaleMode = "linear" | "log" | "percent"

export type Projection = {
  timeToCoordinate: (t: string) => number | null
  priceToCoordinate: (p: number) => number | null
  coordinateToTime: (x: number) => string | null
  coordinateToPrice: (y: number) => number | null
}

const AXIS_W = 62      // right price gutter
const AXIS_H = 22      // bottom time gutter
// Breathing room between the newest bar and the price axis. Reserved in the
// SCALE rather than added as a one-off margin on the initial view, so it
// survives every zoom and pan instead of closing up the first time you touch
// the chart. The grid, the axis labels and the price tag still run the full
// width — the gap is for the series, which otherwise ends flush against the
// numbers describing it.
const AXIS_GAP = 12

export function ChartCanvas({
  bars, kind, scale: scaleMode, height, panes = [],
  gain, loss, accent, grid, text, tagBg, tagFg, tagBorder,
  onProjection, onHover, overlays = [], seriesId, intraday = false,
}: {
  bars: ChartBar[]
  kind: SeriesKind
  scale: ScaleMode
  height: number
  /** Bands stacked under the price, each with its own y scale but sharing the
   * x scale — volume, RSI, MACD, fundamentals. */
  panes?: Pane[]
  gain: string
  loss: string
  accent: string
  grid: string
  text: string
  /** Crosshair chip colours — a muted surface, not the muted text colour. */
  tagBg: string
  tagFg: string
  tagBorder: string
  /** Handed out on every layout change so annotations can project against it. */
  onProjection?: (p: Projection | null) => void
  onHover?: (bar: ChartBar | null, at: { x: number; y: number } | null) => void
  /** Extra lines drawn over the price pane, already in price space. */
  overlays?: { color: string; points: { t: string; v: number }[]; width?: number }[]
  /** Identity of the SERIES — symbol, range and interval. What resets the
   * view. Distinct from `bars`, which now arrives afresh on every poll. */
  seriesId?: string
  /** Whether these bars are INTRADAY. Only then do session boundaries mean
   * anything: on daily bars every bar is already one session. */
  intraday?: boolean
}) {
  const host = useRef<HTMLDivElement>(null)
  const [box, setBox] = useState({ w: 0, h: height })
  const [view, setView] = useState<{ from: number; to: number } | null>(null)
  const [cursor, setCursor] = useState<{ x: number; y: number } | null>(null)
  const drag = useRef<{ x: number; from: number; to: number } | null>(null)
  // State, not just the ref: the grab cursor is rendered, and a ref mutation
  // does not re-render, so reading drag.current in the style never changed it.
  const [panning, setPanning] = useState(false)
  /** Manual price-scale zoom, 1 = fit the visible bars. Dragging the price
   * gutter changes it; double-clicking there puts it back to fitting. */
  const [yZoom, setYZoom] = useState(1)
  const yDrag = useRef<{ y: number; zoom: number } | null>(null)
  /** Rendered, so the ns-resize cursor holds for the WHOLE gesture instead of
   * reverting to the crosshair the moment the pointer leaves the gutter. */
  const [yResizing, setYResizing] = useState(false)
  // One update per frame. A price-scale change invalidates every path string
  // and re-fires the projection effect (a setState in the parent), so running
  // that per mousemove event does several times the work a frame can show.
  const yFrame = useRef<number | null>(null)
  const yNext = useRef<number | null>(null)
  useEffect(() => () => { if (yFrame.current != null) cancelAnimationFrame(yFrame.current) }, [])
  const clipId = useId()

  useEffect(() => {
    if (!host.current) return
    const ro = new ResizeObserver(() => {
      const r = host.current!.getBoundingClientRect()
      setBox({ w: r.width, h: r.height })
    })
    ro.observe(host.current)
    return () => ro.disconnect()
  }, [])

  /** What a new poll must NOT do is move the chart under the reader.
   *
   * Resetting on `bars` was right when the series was fetched once: a new
   * array meant a new series. Now that it refreshes every 30 seconds, a new
   * array usually means the same series with one more bar on it, and resetting
   * there would throw away the reader's zoom twice a minute.
   *
   * So the reset keys on the SERIES identity. Within one series, growth is
   * followed only if the view was already sitting at the live edge — pan the
   * window along so the newest bar stays visible. A reader who has scrolled
   * back to look at yesterday is left exactly where they are.
   */
  const prevSeries = useRef({ id: seriesId, len: bars.length })
  useEffect(() => {
    const was = prevSeries.current
    prevSeries.current = { id: seriesId, len: bars.length }
    if (was.id !== seriesId) {
      setView(bars.length ? { from: 0, to: bars.length } : null)
      // A hand-set price scale belongs to the series it was set on; carrying
      // it onto a different symbol or range would silently misframe the new one.
      setYZoom(1)
      return
    }
    const grew = bars.length - was.len
    if (grew <= 0) return
    setView(v => (v && v.to >= was.len - 0.5 ? { from: v.from + grew, to: v.to + grew } : v))
  }, [seriesId, bars.length])

  const panesH = panes.reduce((n, p) => n + p.height, 0)
  const priceH = Math.max(40, box.h - AXIS_H - panesH)
  const plotW = Math.max(10, box.w - AXIS_W)
  // Where the SERIES lives. `plotW` stays the full drawing width, so anything
  // that belongs to the axis rather than to the data keeps reaching it.
  const seriesW = Math.max(10, plotW - AXIS_GAP)
  const from = view?.from ?? 0
  const to = view?.to ?? Math.max(1, bars.length)

  const { min, max } = useMemo(() => {
    const ext = visibleExtent(bars, from, to)
    const fitted = (() => {
      if (scaleMode !== "percent") return padRange(ext.min, ext.max)
      // Percent rebases to the first visible bar, so two names of very
      // different price can be compared on one axis.
      const base = bars[Math.max(0, Math.floor(from))]?.c
      if (!base) return padRange(ext.min, ext.max)
      return padRange((ext.min / base - 1) * 100, (ext.max / base - 1) * 100)
    })()
    if (yZoom === 1) return fitted
    // Expand or contract about the MIDDLE of the fitted range, so the series
    // stays put while the scale opens up around it rather than sliding.
    const centre = (fitted.min + fitted.max) / 2
    const half = (fitted.max - fitted.min) / 2 / yZoom
    return { min: centre - half, max: centre + half }
  }, [bars, from, to, scaleMode, yZoom])

  // Memoized on its six numbers, and that memo is load-bearing rather than an
  // optimization. `s` feeds the projection effect's dep array, and the effect
  // calls onProjection() — which is a setState in the parent. A fresh object
  // literal here made those deps differ on every render, so the effect re-ran
  // after every commit and set state again: an infinite render loop that React
  // ends by refusing to commit. The first paint had already landed, so the
  // board looked correct and then silently stopped responding to anything —
  // no symbol change, no chip, no toolbar. Keep every field primitive.
  const s: Scale = useMemo(
    () => ({ from, to, width: seriesW, height: priceH, min, max }),
    [from, to, seriesW, priceH, min, max],
  )
  const log = scaleMode === "log"
  const base = bars[Math.max(0, Math.floor(from))]?.c ?? 1
  const toDisplay = useCallback(
    (p: number) => (scaleMode === "percent" ? (p / base - 1) * 100 : p), [scaleMode, base])
  const fromDisplay = useCallback(
    (v: number) => (scaleMode === "percent" ? base * (1 + v / 100) : v), [scaleMode, base])

  const yOf = useCallback((price: number) => priceToY(s, toDisplay(price), log), [s, toDisplay, log])

  // Index lookups for projection. Built once per series rather than scanned.
  const indexByTime = useMemo(() => {
    const m = new Map<string, number>()
    bars.forEach((b, i) => m.set(b.t, i))
    return m
  }, [bars])

  useEffect(() => {
    if (!onProjection) return
    if (!bars.length || !plotW) { onProjection(null); return }
    onProjection({
      timeToCoordinate: t => {
        const i = indexByTime.get(t)
        return i == null ? null : indexToX(s, i + 0.5)
      },
      priceToCoordinate: p => yOf(p),
      coordinateToTime: x => {
        const i = Math.round(xToIndex(s, x) - 0.5)
        return bars[Math.max(0, Math.min(bars.length - 1, i))]?.t ?? null
      },
      coordinateToPrice: y => fromDisplay(yToPrice(s, y, log)),
    })
  }, [onProjection, bars, indexByTime, s, yOf, fromDisplay, log, plotW])

  // ── interaction ─────────────────────────────────────────────────────────

  /** Wheel is bound here rather than as an `onWheel` prop because React
   * registers wheel on the root container as PASSIVE, which makes
   * `preventDefault()` silently do nothing — the chart would zoom while the
   * page scrolled underneath it at the same time. A non-passive listener on
   * the host element is the only way to own the gesture.
   *
   * A PLAIN vertical wheel is deliberately left alone. This chart sits in a
   * scrolling board and can fill most of the viewport once expanded, so
   * claiming that gesture turns the tile into a scroll trap — the page stops
   * moving as soon as the pointer crosses the chart, which is far more
   * annoying than reaching for the range strip. Only gestures the page has no
   * use for are taken:
   *
   *   sideways (trackpad swipe / shift+wheel) → pan. A swipe sends deltaX with
   *     deltaY at ~0; reading only deltaY meant every sideways swipe zoomed IN
   *     instead. The page does not scroll horizontally, so this costs nothing.
   *   ctrl/cmd+wheel → zoom. That is also what a trackpad PINCH reports, so
   *     pinch-to-zoom works without anyone being told about a modifier.
   *
   * preventDefault only fires on those two, which is why the listener must be
   * non-passive: React registers wheel on its root as passive, where
   * preventDefault silently does nothing at all.
   *
   * It must also stay ATTACHED for the whole gesture, which is why the deps
   * below hold nothing that panning changes. With `from`/`to` in there, every
   * wheel event tore the listener down and React re-attached it after the next
   * paint; a trackpad swipe fires ~100 events a second, so events kept landing
   * in the gap unprevented — and on macOS an unprevented horizontal wheel is
   * the back-navigation gesture. Swiping left to pan would leave the page.
   * The window is therefore read inside the state updater rather than closed
   * over here. */
  useEffect(() => {
    const el = host.current
    if (!el) return
    const total = bars.length
    const onWheel = (e: WheelEvent) => {
      if (!total) return
      const sideways = e.shiftKey || Math.abs(e.deltaX) > Math.abs(e.deltaY)
      const zooming = e.ctrlKey || e.metaKey
      if (!sideways && !zooming) return       // the page's gesture, not ours
      e.preventDefault()
      const x = e.clientX - el.getBoundingClientRect().left
      // Whichever axis actually carries the movement. Reading deltaY whenever
      // shift was held looked right and panned by exactly zero: the BROWSER
      // already rewrites a shift+wheel into deltaX, so the axis being read was
      // the one guaranteed to be 0. Platforms that do not rewrite it leave the
      // movement on deltaY, so both have to be tolerated.
      const delta = Math.abs(e.deltaX) >= Math.abs(e.deltaY) ? e.deltaX : e.deltaY
      const factor = e.deltaY > 0 ? 1.15 : 1 / 1.15
      setView(prev => {
        const cur = prev ?? { from: 0, to: total }
        if (sideways && !zooming) {
          const shift = (delta / seriesW) * (cur.to - cur.from)
          return clampView(cur.from + shift, cur.to + shift, total)
        }
        // Only from/to/width are consulted by xToIndex and zoomAt — the price
        // axis plays no part in a horizontal gesture, so it is not rebuilt.
        const sc: Scale = { from: cur.from, to: cur.to, width: seriesW, height: 0, min: 0, max: 0 }
        return zoomAt(sc, xToIndex(sc, x), factor, total)
      })
    }
    el.addEventListener("wheel", onWheel, { passive: false })
    return () => el.removeEventListener("wheel", onWheel)
  }, [bars.length, seriesW])

  const onDown = (e: React.MouseEvent) => {
    drag.current = { x: e.clientX, from, to }
    setPanning(true)
  }
  const onMove = (e: React.MouseEvent) => {
    const r = host.current!.getBoundingClientRect()
    const x = e.clientX - r.left
    const y = e.clientY - r.top
    // The gutter owns this gesture via pointer capture; the host's job is to
    // not draw a crosshair through the middle of it.
    if (yDrag.current) return
    if (drag.current) {
      const span = drag.current.to - drag.current.from
      const shift = ((drag.current.x - e.clientX) / seriesW) * span
      // Clamped like every other view change — an unclamped drag could push
      // the series off the edge and leave a blank chart with no way back.
      setView(clampView(drag.current.from + shift, drag.current.to + shift, bars.length))
      return
    }
    if (x > plotW || y > priceH) { setCursor(null); onHover?.(null, null); return }
    setCursor({ x, y })
    const i = Math.round(xToIndex(s, x) - 0.5)
    onHover?.(bars[Math.max(0, Math.min(bars.length - 1, i))] ?? null, { x, y })
  }
  const stop = () => { drag.current = null; setPanning(false) }
  // Deliberately NOT cleared here or in leave(): the price gutter is a couple
  // of hundred pixels tall and a stretch runs past the tile edge almost at
  // once. Ending the resize on mouseleave made every drag of any size die
  // halfway, which is most of why this felt broken rather than merely fast.
  // Pointer capture keeps delivering to the gutter, and pointerup ends it.
  const leave = () => { stop(); setCursor(null); onHover?.(null, null) }

  // ── geometry, built as path strings ─────────────────────────────────────
  const barW = Math.max(1, (seriesW / Math.max(1, to - from)) * 0.7)
  const half = barW / 2

  const paths = useMemo(() => {
    const upBody: string[] = [], downBody: string[] = []
    const upWick: string[] = [], downWick: string[] = []
    const line: string[] = []
    const lo = Math.max(0, Math.floor(from) - 1)
    const hi = Math.min(bars.length - 1, Math.ceil(to) + 1)

    for (let i = lo; i <= hi; i++) {
      const b = bars[i]
      if (!b) continue
      const x = indexToX(s, i + 0.5)
      if (x < -barW || x > seriesW + barW) continue
      const up = b.c >= b.o
      const yO = yOf(b.o), yC = yOf(b.c), yH = yOf(b.h), yL = yOf(b.l)
      if (kind === "candles" || kind === "bars") {
        const wick = `M${x.toFixed(1)},${yH.toFixed(1)}L${x.toFixed(1)},${yL.toFixed(1)}`
        ;(up ? upWick : downWick).push(wick)
        if (kind === "candles") {
          const top = Math.min(yO, yC)
          const h = Math.max(1, Math.abs(yC - yO))
          ;(up ? upBody : downBody).push(
            `M${(x - half).toFixed(1)},${top.toFixed(1)}h${barW.toFixed(1)}v${h.toFixed(1)}h${(-barW).toFixed(1)}Z`)
        } else {
          // OHLC bars: ticks left for open, right for close.
          ;(up ? upBody : downBody).push(
            `M${(x - half).toFixed(1)},${yO.toFixed(1)}h${half.toFixed(1)}` +
            `M${x.toFixed(1)},${yC.toFixed(1)}h${half.toFixed(1)}`)
        }
      } else {
        line.push(`${line.length ? "L" : "M"}${x.toFixed(1)},${yC.toFixed(1)}`)
      }
    }
    return {
      upBody: upBody.join(""), downBody: downBody.join(""),
      upWick: upWick.join(""), downWick: downWick.join(""),
      line: line.join(""),
      area: line.length ? `${line.join("")}L${seriesW},${priceH}L${indexToX(s, lo + 0.5).toFixed(1)},${priceH}Z` : "",
    }
  }, [bars, s, kind, yOf, barW, half, seriesW, priceH, from, to])

  /** Each pane's own geometry: offset, scale and batched paths. */
  const paneLayout = useMemo(() => {
    let offset = priceH
    return panes.map(pane => {
      const top = offset
      offset += pane.height
      const byTimeIdx = new Map(bars.map((b, i) => [b.t, i]))
      // Bucketed BEFORE the extent is taken: a summed column is taller than any
      // bar inside it, so an extent measured on the raw points would let the
      // columns run straight out of the pane.
      const grouped = new Map<PaneSeries, ReturnType<typeof volumeColumns>>()
      for (const ser of pane.series) {
        if (ser.kind !== "histogram" || !ser.aggregate) continue
        const entries: { i: number; v: number; up: boolean }[] = []
        for (const p of ser.points) {
          const i = byTimeIdx.get(p.t)
          if (i == null || i < from - 1 || i > to + 1) continue
          entries.push({ i, v: p.v, up: bars[i].c >= bars[i].o })
        }
        grouped.set(ser, volumeColumns(entries, s, seriesW, 7))
      }
      // Only when EVERY series was grouped — a pane mixing a summed histogram
      // with a plain line would need both to agree on one axis, and they don't.
      const allGrouped = grouped.size > 0 && grouped.size === pane.series.length
      const ext = allGrouped
        ? { min: 0, max: Math.max(1, ...[...grouped.values()].flat().map(c => c.v)) }
        : paneExtent(pane)
      const inner = Math.max(10, pane.height - 6)
      const yIn = (v: number) => {
        const r = ext.max - ext.min
        return top + 3 + (r <= 0 ? inner / 2 : inner - ((v - ext.min) / r) * inner)
      }
      const zeroY = yIn(Math.min(Math.max(0, ext.min), ext.max))
      const byTime = byTimeIdx
      const drawn = pane.series.map(ser => {
        if (ser.kind === "histogram" && grouped.has(ser)) {
          const up: string[] = [], down: string[] = []
          for (const c of grouped.get(ser)!) {
            if (c.x < -c.w || c.x > seriesW + c.w) continue
            const y = yIn(c.v)
            const h = Math.max(1, Math.abs(zeroY - y))
            const half2 = c.w / 2
            ;(c.up ? up : down).push(
              `M${(c.x - half2).toFixed(1)},${Math.min(zeroY, y).toFixed(1)}h${c.w.toFixed(1)}v${h.toFixed(1)}h${(-c.w).toFixed(1)}Z`)
          }
          return { kind: "histogram" as const, up: up.join(""), down: down.join(""),
                   color: ser.color, downColor: ser.downColor ?? ser.color }
        }
        if (ser.kind === "histogram") {
          const up: string[] = [], down: string[] = []
          for (const p of ser.points) {
            const i = byTime.get(p.t)
            if (i == null) continue
            const x = indexToX(s, i + 0.5)
            if (x < -barW || x > seriesW + barW) continue
            const y = yIn(p.v)
            const h = Math.max(1, Math.abs(zeroY - y))
            const topY = Math.min(zeroY, y)
            // `signs` decides what "up" means: the value's own sign for MACD
            // and fundamentals, the bar's direction for volume.
            const isUp = ser.signs ? p.v >= 0 : (bars[i].c >= bars[i].o)
            ;(isUp ? up : down).push(
              `M${(x - half).toFixed(1)},${topY.toFixed(1)}h${barW.toFixed(1)}v${h.toFixed(1)}h${(-barW).toFixed(1)}Z`)
          }
          return { kind: "histogram" as const, up: up.join(""), down: down.join(""),
                   color: ser.color, downColor: ser.downColor ?? ser.color }
        }
        const d = ser.points.map((p, n) => {
          const i = byTime.get(p.t)
          if (i == null) return ""
          return `${n === 0 ? "M" : "L"}${indexToX(s, i + 0.5).toFixed(1)},${yIn(p.v).toFixed(1)}`
        }).filter(Boolean).join("")
        const areaD = ser.kind === "area" && d
          ? `${d}L${seriesW},${top + pane.height}L0,${top + pane.height}Z` : ""
        return { kind: ser.kind, d, areaD, color: ser.color, width: (ser as { width?: number }).width ?? 1.5 }
      })
      const levels = (pane.levels ?? []).map(v => ({ v, y: yIn(v) }))
      const axis = [ext.min, (ext.min + ext.max) / 2, ext.max]
        .map(v => ({ v, y: yIn(v) }))
      return { pane, top, drawn, levels, axis }
    })
  }, [panes, priceH, bars, s, barW, half, seriesW, from, to])

  /** Axis rows covered by a price tag.
   *
   * The last-price tag and the crosshair tag are opaque chips pinned to the
   * gutter at whatever price they mark, so whenever that price sits near a
   * round number the chip lands on top of the tick label and the two render
   * over each other. The tick is the one that gives way: it is a fixed
   * gridline a reader can infer from its neighbours, while the tag carries the
   * number actually being asked about.
   *
   * 12px is the tag's half-height (8) plus half a line of 11px text, so a
   * label is dropped exactly when it would collide rather than whenever it is
   * merely close. */
  const TAG_HALF = 12
  const tagRows: number[] = []
  const ticks = priceTicks(min, max)
  const decimals = priceDecimals(ticks.length > 1 ? Math.abs(ticks[1] - ticks[0]) : 1)
  const last = bars[bars.length - 1]
  if (last) tagRows.push(yOf(last.c))
  if (cursor) tagRows.push(cursor.y)
  const tagHides = (y: number) => tagRows.some(t => Math.abs(y - t) < TAG_HALF)
  const hoveredIdx = cursor
    ? Math.max(0, Math.min(bars.length - 1, Math.round(xToIndex(s, cursor.x) - 0.5)))
    : -1
  const hovered = cursor ? bars[hoveredIdx] : null

  /** Time labels, thinned to whatever fits without collision. */
  /** Session shading and day breaks. Memoized on the bars because it walks
   * every one of them through a timezone conversion — cheap once, wasteful on
   * each of the thirty-odd renders a minute the live feed now causes. */
  const sessions = useMemo(
    () => (intraday ? sessionLayout(bars) : { bands: [], dividers: [] }),
    [bars, intraday],
  )

  const timeTicks = useMemo(() => {
    const out: { x: number; label: string }[] = []
    const span = to - from
    const stride = Math.max(1, Math.round(span / Math.max(2, Math.floor(seriesW / 90))))
    const daily = span > 400 || (bars.length > 1 &&
      Date.parse(bars[bars.length - 1].t) - Date.parse(bars[bars.length - 2].t) >= 86_400_000)
    for (let i = Math.max(0, Math.ceil(from)); i < Math.min(bars.length, to); i += stride) {
      const d = new Date(bars[i].t)
      out.push({
        x: indexToX(s, i + 0.5),
        label: daily
          ? d.toLocaleDateString("en-US", { month: "short", day: "numeric", timeZone: "America/New_York" })
          : d.toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit", timeZone: "America/New_York" }),
      })
    }
    return out
  }, [bars, s, from, to, seriesW])

  return (
    <div
      ref={host}
      className="relative w-full select-none"
      style={{ height, cursor: yResizing ? "ns-resize" : panning ? "grabbing" : "crosshair" }}
      onMouseDown={onDown}
      onMouseMove={onMove}
      onMouseUp={stop}
      onMouseLeave={leave}
    >
      <svg width="100%" height={height} className="block">
        <defs>
          {/* Everything that draws data is clipped to the plot. Without it a
              panned series runs straight under the price gutter and collides
              with the axis labels and the price tag — the bars are still drawn
              past the edge on purpose (a partially visible candle at the edge
              is correct), they just must not be VISIBLE there. */}
          <clipPath id={clipId}>
            <rect x={0} y={0} width={plotW} height={priceH + panesH} />
          </clipPath>
        </defs>

        {/* horizontal grid + price axis */}
        {ticks.map(v => {
          const y = priceToY(s, v, log)
          return (
            <g key={v}>
              {/* The gridline stays either way — it is behind the plot, not
                  under the chip, and losing it would leave a visible hole in
                  the grid every time the price passed a round number. */}
              <line x1={0} y1={y} x2={plotW} y2={y} stroke={grid} strokeWidth={1} />
              {!tagHides(y) && (
              <text x={plotW + 6} y={y + 3.5} fill={text} fontSize={11} className="tnum">
                {scaleMode === "percent" ? `${v.toFixed(1)}%` : v.toFixed(decimals)}
              </text>
              )}
            </g>
          )
        })}

        {/* vertical grid + time axis */}
        {/* A TICK below the axis, not a rule through the plot. The horizontal
            grid is what a price is read against; the vertical one was crossing
            every candle and every indicator line without any reading being
            taken against it, which is most of what made this canvas busier
            than theirs. The tick still says where the label points. */}
        {timeTicks.map((t, i) => (
          <g key={i}>
            <line x1={t.x} y1={priceH + panesH} x2={t.x} y2={priceH + panesH + 4}
                  stroke={grid} strokeWidth={1} />
            <text x={t.x} y={priceH + panesH + 16} fill={text} fontSize={11} textAnchor="middle" className="tnum">
              {t.label}
            </text>
          </g>
        ))}

        <g clipPath={`url(#${clipId})`}>
        {/* Extended hours, and the breaks between trading days.
            Drawn FIRST so they sit behind the data: this is context for
            reading the series, not a thing to read on its own. The x scale is
            bar index, so a night between a 16:00 close and the next 09:30 open
            is one bar wide and otherwise invisible — the divider is what keeps
            an overnight gap from reading as a minute's move. */}
        {sessions.bands.map((b, i) => {
          const x0 = indexToX(s, b.from)
          const x1 = indexToX(s, b.to)
          if (x1 < 0 || x0 > seriesW) return null
          return (
            <rect key={`b${i}`} x={x0} y={0} width={Math.max(1, x1 - x0)}
              height={priceH + panesH} fill={tagBg} opacity={0.55}>
              <title>{b.kind === "pre" ? "Pre-market" : "After hours"}</title>
            </rect>
          )
        })}
        {sessions.dividers.map((idx, i) => {
          const x = indexToX(s, idx)
          if (x < 0 || x > seriesW) return null
          return (
            <line key={`d${i}`} x1={x} y1={0} x2={x} y2={priceH + panesH}
              stroke={text} strokeWidth={1} strokeDasharray="2 4" opacity={0.35} />
          )
        })}

        {/* the series */}
        {kind === "area" && <path d={paths.area} fill={accent} fillOpacity={0.14} />}
        {(kind === "line" || kind === "area") && (
          <path d={paths.line} fill="none" stroke={accent} strokeWidth={1.5}
            strokeLinejoin="round" strokeLinecap="round" />
        )}
        {(kind === "candles" || kind === "bars") && (
          <>
            <path d={paths.upWick} stroke={gain} strokeWidth={1} fill="none" />
            <path d={paths.downWick} stroke={loss} strokeWidth={1} fill="none" />
            {kind === "candles" ? (
              <>
                <path d={paths.upBody} fill={gain} />
                <path d={paths.downBody} fill={loss} />
              </>
            ) : (
              <>
                <path d={paths.upBody} stroke={gain} strokeWidth={1.2} fill="none" />
                <path d={paths.downBody} stroke={loss} strokeWidth={1.2} fill="none" />
              </>
            )}
          </>
        )}

        {/* indicator overlays, in price space */}
        {overlays.map((o, i) => {
          const d = o.points.map((p, n) => {
            const idx = indexByTime.get(p.t)
            if (idx == null) return ""
            return `${n === 0 ? "M" : "L"}${indexToX(s, idx + 0.5).toFixed(1)},${yOf(p.v).toFixed(1)}`
          }).filter(Boolean).join("")
          return <path key={i} d={d} fill="none" stroke={o.color} strokeWidth={o.width ?? 1} />
        })}

        {/* the stacked panes */}
        {paneLayout.map(({ pane, top, drawn, levels, axis }) => (
          <g key={pane.id}>
            <line x1={0} y1={top} x2={plotW} y2={top} stroke={grid} strokeWidth={1} />
            {levels.map(l => (
              <line key={l.v} x1={0} y1={l.y} x2={plotW} y2={l.y}
                stroke={text} strokeOpacity={0.35} strokeWidth={1} strokeDasharray="3 3" />
            ))}
            {axis.map((a, i) => (
              tagHides(a.y) ? null : (
                <text key={i} x={plotW + 6} y={a.y + 3.5} fill={text} fontSize={10} className="tnum">
                  {paneAxisLabel(a.v, pane.compact)}
                </text>
              )
            ))}
            {drawn.map((d, i) =>
              d.kind === "histogram" ? (
                <g key={i}>
                  {/* Lighter than a candle body: the volume band is context
                      for the price above it, not a second thing to read. */}
                  <path d={d.up} fill={d.color} fillOpacity={0.42} />
                  <path d={d.down} fill={d.downColor} fillOpacity={0.42} />
                </g>
              ) : (
                <g key={i}>
                  {d.kind === "area" && d.areaD && <path d={d.areaD} fill={d.color} fillOpacity={0.16} />}
                  <path d={d.d} fill="none" stroke={d.color} strokeWidth={d.width} />
                </g>
              ))}
            {pane.label && (
              <text x={4} y={top + 12} fill={text} fontSize={10}>{pane.label}</text>
            )}
          </g>
        ))}

        </g>

        {/* last price, tagged on the axis */}
        {last && (
          <g>
            <line x1={0} y1={yOf(last.c)} x2={plotW} y2={yOf(last.c)}
              stroke={accent} strokeWidth={1} strokeDasharray="2 3" />
            <rect x={plotW + 2} y={yOf(last.c) - 8} width={AXIS_W - 4} height={16} fill={accent} rx={2} />
            <text x={plotW + 6} y={yOf(last.c) + 3.5} fill="#fff" fontSize={11} className="tnum">
              {(scaleMode === "percent" ? toDisplay(last.c) : last.c).toFixed(decimals)}
            </text>
          </g>
        )}

        {/* crosshair */}
        {cursor && (
          <g pointerEvents="none">
            <line x1={cursor.x} y1={0} x2={cursor.x} y2={priceH + panesH}
              stroke={text} strokeWidth={1} strokeDasharray="3 3" />
            <line x1={0} y1={cursor.y} x2={plotW} y2={cursor.y}
              stroke={text} strokeWidth={1} strokeDasharray="3 3" />
            {/* The bar being read, marked ON the series.
                The vertical rule says which COLUMN the cursor is in, which is
                not the same as which point the OHLCV strip is quoting — at a
                few bars to the pixel they can differ by several bars, and the
                rule alone leaves the reader to guess where on the line the
                numbers came from. Only on the line kinds: a candle already
                shows its own close, and a dot on top of one would just cover
                the thing it was pointing at. */}
            {hovered && (kind === "line" || kind === "area") && (
              <circle
                cx={indexToX(s, hoveredIdx + 0.5)} cy={yOf(hovered.c)} r={3.5}
                fill={accent} stroke={tagBg} strokeWidth={2}
              />
            )}
            <rect x={plotW + 2} y={cursor.y - 8} width={AXIS_W - 4} height={16}
              fill={tagBg} stroke={tagBorder} strokeWidth={1} rx={2} />
            <text x={plotW + 6} y={cursor.y + 3.5} fill={tagFg} fontSize={11} className="tnum">
              {yToPrice(s, cursor.y, log).toFixed(decimals)}
            </text>
            {hovered && (
              <>
                <rect x={cursor.x - 42} y={priceH + panesH + 3} width={84} height={16}
                  fill={tagBg} stroke={tagBorder} strokeWidth={1} rx={2} />
                <text x={cursor.x} y={priceH + panesH + 14.5} fill={tagFg} fontSize={11}
                  textAnchor="middle" className="tnum">
                  {new Date(hovered.t).toLocaleString("en-US", {
                    timeZone: "America/New_York", month: "short", day: "numeric",
                    hour: "numeric", minute: "2-digit",
                  })}
                </text>
              </>
            )}
          </g>
        )}

        {/* The price gutter, grabbable. Rendered LAST so it is on top of the
            axis labels: a transparent rect still takes the pointer, and if the
            labels sat above it a drag begun exactly on "44.00" would fall
            through to the pan handler instead. */}
        <rect
          x={plotW} y={0} width={AXIS_W} height={priceH}
          fill="transparent"
          style={{ cursor: "ns-resize", touchAction: "none" }}
          onPointerDown={e => {
            // Capture, so the rest of the gesture keeps arriving here even once
            // the pointer is off the gutter, off the tile, or over the AI rail.
            // Without it a stretch of any real size ended the moment the cursor
            // crossed the chart's edge.
            e.stopPropagation()
            ;(e.currentTarget as Element).setPointerCapture(e.pointerId)
            yDrag.current = { y: e.clientY, zoom: yZoom }
            setYResizing(true)
          }}
          onPointerMove={e => {
            if (!yDrag.current) return
            e.stopPropagation()
            // Exponential: the same drag distance is the same proportional
            // change at every zoom level, and the factor can never reach zero
            // and invert the axis. Down compresses, matching theirs.
            const dy = e.clientY - yDrag.current.y
            yNext.current = Math.min(8, Math.max(0.2, yDrag.current.zoom * Math.exp(-dy / 160)))
            if (yFrame.current == null) {
              yFrame.current = requestAnimationFrame(() => {
                yFrame.current = null
                if (yNext.current != null) setYZoom(yNext.current)
              })
            }
          }}
          onPointerUp={e => {
            e.stopPropagation()
            ;(e.currentTarget as Element).releasePointerCapture(e.pointerId)
            yDrag.current = null
            setYResizing(false)
          }}
          onPointerCancel={() => { yDrag.current = null; setYResizing(false) }}
          onDoubleClick={e => { e.stopPropagation(); setYZoom(1) }}
        >
          <title>Drag to stretch the price scale · double-click to fit</title>
        </rect>
      </svg>
    </div>
  )
}
