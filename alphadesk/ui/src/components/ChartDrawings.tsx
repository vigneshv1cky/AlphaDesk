import { useCallback, useEffect, useRef, useState } from "react"
import type { IChartApi, ISeriesApi, SeriesType, Time } from "lightweight-charts"

/** Hand-drawn annotations over the price pane.
 *
 * Shapes are stored in DATA coordinates — a (time, price) pair per anchor —
 * and re-projected to pixels on every pan, zoom and resize. Storing pixels
 * would be far simpler and completely wrong: the drawing would slide off the
 * bar it was drawn against the moment the chart moved, which is the one thing
 * an annotation must never do.
 *
 * Rendered as an SVG overlay rather than through the library's canvas
 * primitive API. The primitives API means implementing a renderer against its
 * canvas target for every shape; an overlay gets hit-testing, hover states and
 * crisp text from the browser for free, and the projection work is identical
 * either way.
 */

export type Tool = "none" | "hline" | "trend" | "rect"

type Anchor = { time: Time; price: number }
export type Drawing =
  | { id: string; kind: "hline"; a: Anchor }
  | { id: string; kind: "trend"; a: Anchor; b: Anchor }
  | { id: string; kind: "rect"; a: Anchor; b: Anchor }

let seq = 0
const nextId = () => `d${++seq}`

export function ChartDrawings({
  chart, series, tool, onToolDone, drawings, onChange, visible, height,
}: {
  chart: IChartApi | null
  series: ISeriesApi<SeriesType> | null
  tool: Tool
  /** Fired after a shape completes, so the toolbar can drop back to the
   * crosshair — matching how every charting tool behaves. */
  onToolDone: () => void
  drawings: Drawing[]
  onChange: (next: Drawing[]) => void
  visible: boolean
  height: number
}) {
  const wrap = useRef<HTMLDivElement>(null)
  const [pending, setPending] = useState<Anchor | null>(null)
  const [cursor, setCursor] = useState<{ x: number; y: number } | null>(null)
  // Bumped whenever the chart moves, to force a re-projection.
  const [, setTick] = useState(0)

  useEffect(() => {
    if (!chart) return
    const redraw = () => setTick(t => t + 1)
    chart.timeScale().subscribeVisibleLogicalRangeChange(redraw)
    const ro = new ResizeObserver(redraw)
    if (wrap.current) ro.observe(wrap.current)
    return () => {
      chart.timeScale().unsubscribeVisibleLogicalRangeChange(redraw)
      ro.disconnect()
    }
  }, [chart])

  /** data -> pixels. Null when the anchor is scrolled out of view. */
  const project = useCallback((a: Anchor): { x: number; y: number } | null => {
    if (!chart || !series) return null
    const x = chart.timeScale().timeToCoordinate(a.time)
    const y = series.priceToCoordinate(a.price)
    return x == null || y == null ? null : { x, y }
  }, [chart, series])

  /** pixels -> data. Null outside the plotted area. */
  const unproject = useCallback((x: number, y: number): Anchor | null => {
    if (!chart || !series) return null
    const time = chart.timeScale().coordinateToTime(x)
    const price = series.coordinateToPrice(y)
    return time == null || price == null ? null : { time, price }
  }, [chart, series])

  const onClick = (e: React.MouseEvent) => {
    if (tool === "none") return
    const r = wrap.current!.getBoundingClientRect()
    const at = unproject(e.clientX - r.left, e.clientY - r.top)
    if (!at) return
    if (tool === "hline") {
      onChange([...drawings, { id: nextId(), kind: "hline", a: at }])
      onToolDone()
      return
    }
    // Two-click shapes: first click anchors, second completes.
    if (!pending) { setPending(at); return }
    onChange([...drawings, { id: nextId(), kind: tool, a: pending, b: at } as Drawing])
    setPending(null)
    onToolDone()
  }

  const onMove = (e: React.MouseEvent) => {
    if (tool === "none" || !pending) { setCursor(null); return }
    const r = wrap.current!.getBoundingClientRect()
    setCursor({ x: e.clientX - r.left, y: e.clientY - r.top })
  }

  // Escape abandons a half-drawn shape rather than leaving it armed.
  useEffect(() => {
    const esc = (e: KeyboardEvent) => { if (e.key === "Escape") { setPending(null); onToolDone() } }
    document.addEventListener("keydown", esc)
    return () => document.removeEventListener("keydown", esc)
  }, [onToolDone])

  const width = wrap.current?.clientWidth ?? 0
  const stroke = "var(--accent)"

  return (
    <div
      ref={wrap}
      onClick={onClick}
      onMouseMove={onMove}
      className="absolute inset-0"
      style={{
        height,
        // Above the chart's own canvases, which carry their own stacking and
        // would otherwise swallow the click that places a drawing.
        zIndex: 10,
        // Only intercept the mouse while a tool is armed — otherwise the chart
        // keeps its own crosshair, pan and zoom.
        pointerEvents: tool === "none" ? "none" : "auto",
        cursor: tool === "none" ? "default" : "crosshair",
      }}
    >
      {visible && (
        <svg width="100%" height={height} className="pointer-events-none absolute inset-0">
          {drawings.map(d => {
            const a = project(d.a)
            if (!a) return null
            if (d.kind === "hline") {
              return (
                <g key={d.id}>
                  <line x1={0} y1={a.y} x2={width} y2={a.y} stroke={stroke} strokeWidth={1} strokeDasharray="4 3" />
                  <text x={6} y={a.y - 4} fill={stroke} fontSize={11} className="tnum">
                    {d.a.price.toFixed(2)}
                  </text>
                </g>
              )
            }
            const b = project(d.b)
            if (!b) return null
            if (d.kind === "trend") {
              return <line key={d.id} x1={a.x} y1={a.y} x2={b.x} y2={b.y} stroke={stroke} strokeWidth={1.5} />
            }
            return (
              <rect
                key={d.id}
                x={Math.min(a.x, b.x)} y={Math.min(a.y, b.y)}
                width={Math.abs(b.x - a.x)} height={Math.abs(b.y - a.y)}
                fill={stroke} fillOpacity={0.1} stroke={stroke} strokeWidth={1}
              />
            )
          })}

          {/* The shape being drawn, following the cursor. */}
          {pending && cursor && (() => {
            const a = project(pending)
            if (!a) return null
            if (tool === "trend") {
              return <line x1={a.x} y1={a.y} x2={cursor.x} y2={cursor.y} stroke={stroke} strokeWidth={1.5} strokeDasharray="4 3" />
            }
            if (tool === "rect") {
              return (
                <rect
                  x={Math.min(a.x, cursor.x)} y={Math.min(a.y, cursor.y)}
                  width={Math.abs(cursor.x - a.x)} height={Math.abs(cursor.y - a.y)}
                  fill={stroke} fillOpacity={0.08} stroke={stroke} strokeWidth={1} strokeDasharray="4 3"
                />
              )
            }
            return null
          })()}
        </svg>
      )}
    </div>
  )
}

/** The floating tool strip. Theirs sits over the chart rather than in the
 * toolbar, so it is close to the hand that is drawing.
 *
 * Only tools that actually work are offered. A rail of thirteen icons where
 * three do something is the same failure as a menu that opens onto nothing. */
export function DrawingToolbar({
  tool, onTool, visible, onVisible, count, onClear, onClose,
}: {
  tool: Tool
  onTool: (t: Tool) => void
  visible: boolean
  onVisible: (v: boolean) => void
  count: number
  onClear: () => void
  onClose: () => void
}) {
  const TOOLS: { id: Tool; label: string; glyph: string }[] = [
    { id: "none", label: "Crosshair", glyph: "✛" },
    { id: "trend", label: "Trend line", glyph: "╱" },
    { id: "hline", label: "Horizontal line", glyph: "─" },
    { id: "rect", label: "Rectangle", glyph: "▭" },
  ]
  const cls = (on: boolean) =>
    `flex h-7 w-7 items-center justify-center rounded text-[13px] transition-colors ${
      on ? "bg-accent/20 text-accent" : "text-muted-foreground hover:bg-muted hover:text-foreground"
    }`
  return (
    <div className="absolute bottom-3 left-3 z-20 flex items-center gap-0.5 rounded-lg border border-border bg-popover/95 px-1.5 py-1 shadow-lg backdrop-blur">
      {TOOLS.map(t => (
        <button key={t.id} type="button" title={t.label} aria-label={t.label}
          aria-pressed={tool === t.id} onClick={() => onTool(t.id)} className={cls(tool === t.id)}>
          {t.glyph}
        </button>
      ))}
      <span className="mx-1 h-5 w-px bg-border" />
      <button type="button" title={visible ? "Hide drawings" : "Show drawings"}
        aria-label={visible ? "Hide drawings" : "Show drawings"}
        onClick={() => onVisible(!visible)} className={cls(!visible)}>
        {visible ? "◉" : "◎"}
      </button>
      <button type="button" title={count ? `Clear ${count} drawing${count === 1 ? "" : "s"}` : "Nothing to clear"}
        aria-label="Clear drawings" onClick={onClear} disabled={!count}
        className={`${cls(false)} disabled:opacity-30`}>
        ⌫
      </button>
      <button type="button" title="Close drawing tools" aria-label="Close drawing tools"
        onClick={onClose} className={cls(false)}>
        ✕
      </button>
    </div>
  )
}
