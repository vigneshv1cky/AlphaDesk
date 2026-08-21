import { useCallback, useEffect, useRef, useState } from "react"
import type { Projection } from "@/components/chart/ChartCanvas"

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

/** Lines and a measure, and nothing else.
 *
 * Rectangles, ellipses, Fibonacci retracements and free text were here and are
 * gone. They are annotation — decoration over a chart — where a line marks a
 * level you are actually reading against and the measure answers a question
 * with a number. This terminal reads market data rather than presenting it to
 * anyone, so the shapes had no audience. Git history has them.
 */
export type Tool =
  | "none" | "hline" | "vline" | "trend" | "ray" | "measure"

/** Tools that need a second click to complete. The rest place on one. */
const TWO_CLICK: Tool[] = ["trend", "ray", "measure"]

type Anchor = { time: string; price: number }
export type Drawing = {
  id: string
  kind: Exclude<Tool, "none">
  a: Anchor
  b?: Anchor
}

let seq = 0
const nextId = () => `d${++seq}`

export function ChartDrawings({
  projection, tool, onToolDone, drawings, onChange, visible, height,
}: {
  /** Supplied by the renderer. Null before the first layout. */
  projection: Projection | null
  tool: Tool
  /** Fired after a shape completes, so the toolbar drops back to the crosshair
   * — matching how every charting tool behaves. */
  onToolDone: () => void
  drawings: Drawing[]
  onChange: (next: Drawing[]) => void
  visible: boolean
  height: number
}) {
  const wrap = useRef<HTMLDivElement>(null)
  const [pending, setPending] = useState<Anchor | null>(null)
  const [cursor, setCursor] = useState<{ x: number; y: number } | null>(null)
  /** Where a text label is being typed, before it becomes a drawing. */
  // Bumped whenever the chart moves, to force a re-projection.
  const [, setTick] = useState(0)

  // The renderer hands out a fresh projection on every pan, zoom and resize,
  // so re-projecting is just re-rendering when that identity changes.
  useEffect(() => { setTick(t => t + 1) }, [projection])

  /** data -> pixels. Null when the anchor is scrolled out of view. */
  const project = useCallback((a: Anchor) => {
    if (!projection) return null
    const x = projection.timeToCoordinate(a.time)
    const y = projection.priceToCoordinate(a.price)
    return x == null || y == null ? null : { x, y }
  }, [projection])

  /** pixels -> data. Null outside the plotted area. */
  const unproject = useCallback((x: number, y: number): Anchor | null => {
    if (!projection) return null
    const time = projection.coordinateToTime(x)
    const price = projection.coordinateToPrice(y)
    return time == null || price == null ? null : { time, price }
  }, [projection])

  const commit = (d: Drawing) => { onChange([...drawings, d]); onToolDone() }

  const onClick = (e: React.MouseEvent) => {
    if (tool === "none") return
    const r = wrap.current!.getBoundingClientRect()
    const px = { x: e.clientX - r.left, y: e.clientY - r.top }
    const at = unproject(px.x, px.y)
    if (!at) return

    if (!TWO_CLICK.includes(tool)) { commit({ id: nextId(), kind: tool, a: at }); return }
    if (!pending) { setPending(at); return }
    commit({ id: nextId(), kind: tool, a: pending, b: at })
    setPending(null)
  }

  const onMove = (e: React.MouseEvent) => {
    if (tool === "none" || !pending) { setCursor(null); return }
    const r = wrap.current!.getBoundingClientRect()
    setCursor({ x: e.clientX - r.left, y: e.clientY - r.top })
  }

  // Escape abandons a half-drawn shape rather than leaving it armed.
  useEffect(() => {
    const esc = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return
      setPending(null); onToolDone()
    }
    document.addEventListener("keydown", esc)
    return () => document.removeEventListener("keydown", esc)
  }, [onToolDone])

  const width = wrap.current?.clientWidth ?? 0
  const stroke = "var(--accent)"

  /** Elapsed time between two anchors, for the measure readout. Anchors carry
   * an ISO timestamp, so this is real elapsed time rather than a bar count —
   * which is the honest number when the series has gaps in it. */
  const spanLabel = (a: Anchor, b: Anchor) => {
    const secs = Math.abs(Date.parse(b.time) - Date.parse(a.time)) / 1000
    if (secs >= 86400) return `${(secs / 86400).toFixed(1)}d`
    if (secs >= 3600) return `${(secs / 3600).toFixed(1)}h`
    return `${Math.round(secs / 60)}m`
  }

  const shape = (d: Drawing, ghost = false) => {
    const a = project(d.a)
    if (!a) return null
    const dash = ghost ? "4 3" : undefined
    const op = ghost ? 0.7 : 1

    if (d.kind === "hline") {
      return (
        <g key={d.id} opacity={op}>
          <line x1={0} y1={a.y} x2={width} y2={a.y} stroke={stroke} strokeWidth={1} strokeDasharray="4 3" />
          <text x={6} y={a.y - 4} fill={stroke} fontSize={11} className="tnum">{d.a.price.toFixed(2)}</text>
        </g>
      )
    }
    if (d.kind === "vline") {
      return <line key={d.id} x1={a.x} y1={0} x2={a.x} y2={height} stroke={stroke} strokeWidth={1} strokeDasharray="4 3" opacity={op} />
    }

    const bAnchor = ghost && cursor ? null : d.b
    const b = bAnchor ? project(bAnchor) : cursor
    if (!b) return null

    if (d.kind === "trend") {
      return <line key={d.id} x1={a.x} y1={a.y} x2={b.x} y2={b.y} stroke={stroke} strokeWidth={1.5} strokeDasharray={dash} opacity={op} />
    }
    if (d.kind === "ray") {
      // Extended to the right edge along the same slope — a ray is a trend
      // line that keeps its claim about the future.
      const dx = b.x - a.x
      const t = dx === 0 ? 0 : (width - a.x) / dx
      const end = dx === 0 ? { x: a.x, y: height } : { x: width, y: a.y + (b.y - a.y) * t }
      return (
        <g key={d.id} opacity={op}>
          <line x1={a.x} y1={a.y} x2={end.x} y2={end.y} stroke={stroke} strokeWidth={1.5} strokeDasharray={dash} />
          <circle cx={a.x} cy={a.y} r={2.5} fill={stroke} />
        </g>
      )
    }
    const box = {
      x: Math.min(a.x, b.x), y: Math.min(a.y, b.y),
      w: Math.abs(b.x - a.x), h: Math.abs(b.y - a.y),
    }
    // measure
    const from = d.a.price
    const to = bAnchor?.price ?? d.a.price
    const pct = from === 0 ? 0 : ((to - from) / Math.abs(from)) * 100
    const up = to >= from
    return (
      <g key={d.id}>
        <rect {...{ x: box.x, y: box.y, width: box.w, height: box.h }}
          fill={up ? "var(--gain)" : "var(--loss)"} fillOpacity={0.12}
          stroke={up ? "var(--gain)" : "var(--loss)"} strokeWidth={1} strokeDasharray={dash} />
        <text x={box.x + box.w / 2} y={box.y - 5} textAnchor="middle"
          fill={up ? "var(--gain)" : "var(--loss)"} fontSize={11} className="tnum">
          {up ? "+" : ""}{(to - from).toFixed(2)} ({up ? "+" : ""}{pct.toFixed(2)}%)
          {bAnchor ? ` · ${spanLabel(d.a, bAnchor)}` : ""}
        </text>
      </g>
    )
  }

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
          {drawings.map(d => shape(d))}
          {pending && cursor && shape({ id: "ghost", kind: tool as Drawing["kind"], a: pending }, true)}
        </svg>
      )}

    </div>
  )
}

/** The floating tool strip. Theirs sits over the chart rather than in the
 * toolbar, so it is close to the hand that is drawing. */
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
    { id: "ray", label: "Ray", glyph: "➚" },
    { id: "hline", label: "Horizontal line", glyph: "─" },
    { id: "vline", label: "Vertical line", glyph: "│" },
    { id: "measure", label: "Measure", glyph: "⇕" },
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
