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

export type Tool =
  | "none" | "hline" | "vline" | "trend" | "ray"
  | "rect" | "ellipse" | "fib" | "measure" | "text"

/** Tools that need a second click to complete. The rest place on one. */
const TWO_CLICK: Tool[] = ["trend", "ray", "rect", "ellipse", "fib", "measure"]

type Anchor = { time: Time; price: number }
export type Drawing = {
  id: string
  kind: Exclude<Tool, "none">
  a: Anchor
  b?: Anchor
  text?: string
}

/** Fibonacci retracement levels. 0 and 1 are the anchors themselves; the rest
 * are the conventional ratios, which is why they are pinned rather than
 * configurable — a "fib" drawn on other numbers is not a fib. */
const FIB = [0, 0.236, 0.382, 0.5, 0.618, 0.786, 1]

let seq = 0
const nextId = () => `d${++seq}`

export function ChartDrawings({
  chart, series, tool, onToolDone, drawings, onChange, visible, height,
}: {
  chart: IChartApi | null
  series: ISeriesApi<SeriesType> | null
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
  const [typing, setTyping] = useState<{ at: Anchor; x: number; y: number } | null>(null)
  const [draft, setDraft] = useState("")
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
  const project = useCallback((a: Anchor) => {
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

  const commit = (d: Drawing) => { onChange([...drawings, d]); onToolDone() }

  const onClick = (e: React.MouseEvent) => {
    if (tool === "none" || typing) return
    const r = wrap.current!.getBoundingClientRect()
    const px = { x: e.clientX - r.left, y: e.clientY - r.top }
    const at = unproject(px.x, px.y)
    if (!at) return

    if (tool === "text") { setTyping({ at, ...px }); setDraft(""); return }
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
      setPending(null); setTyping(null); onToolDone()
    }
    document.addEventListener("keydown", esc)
    return () => document.removeEventListener("keydown", esc)
  }, [onToolDone])

  const width = wrap.current?.clientWidth ?? 0
  const stroke = "var(--accent)"
  const muted = "var(--muted-foreground)"

  /** Bars between two anchors, for the measure readout. Times are unix seconds
   * on this chart, so the difference is real elapsed time rather than an index
   * count — reported as duration, which is what it honestly is. */
  const spanLabel = (a: Anchor, b: Anchor) => {
    const secs = Math.abs(Number(b.time) - Number(a.time))
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
    if (d.kind === "text") {
      return (
        <text key={d.id} x={a.x} y={a.y} fill={stroke} fontSize={12} opacity={op}>
          {d.text}
        </text>
      )
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
    if (d.kind === "rect") {
      return <rect key={d.id} {...{ x: box.x, y: box.y, width: box.w, height: box.h }}
        fill={stroke} fillOpacity={ghost ? 0.08 : 0.1} stroke={stroke} strokeWidth={1} strokeDasharray={dash} />
    }
    if (d.kind === "ellipse") {
      return <ellipse key={d.id} cx={box.x + box.w / 2} cy={box.y + box.h / 2} rx={box.w / 2} ry={box.h / 2}
        fill={stroke} fillOpacity={ghost ? 0.08 : 0.1} stroke={stroke} strokeWidth={1} strokeDasharray={dash} />
    }
    if (d.kind === "fib") {
      const hi = Math.max(d.a.price, bAnchor?.price ?? d.a.price)
      const lo = Math.min(d.a.price, bAnchor?.price ?? d.a.price)
      if (ghost) return <rect key={d.id} {...{ x: box.x, y: box.y, width: box.w, height: box.h }}
        fill={stroke} fillOpacity={0.06} stroke={stroke} strokeDasharray="4 3" strokeWidth={1} />
      return (
        <g key={d.id}>
          {FIB.map(level => {
            const price = hi - (hi - lo) * level
            const p = project({ time: d.a.time, price })
            if (!p) return null
            return (
              <g key={level}>
                <line x1={box.x} y1={p.y} x2={box.x + box.w} y2={p.y}
                  stroke={stroke} strokeWidth={1} strokeOpacity={level === 0 || level === 1 ? 0.9 : 0.5} />
                <text x={box.x + box.w + 4} y={p.y + 3} fill={muted} fontSize={10} className="tnum">
                  {(level * 100).toFixed(1)}% · {price.toFixed(2)}
                </text>
              </g>
            )
          })}
        </g>
      )
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
        pointerEvents: tool === "none" && !typing ? "none" : "auto",
        cursor: tool === "none" ? "default" : "crosshair",
      }}
    >
      {visible && (
        <svg width="100%" height={height} className="pointer-events-none absolute inset-0">
          {drawings.map(d => shape(d))}
          {pending && cursor && shape({ id: "ghost", kind: tool as Drawing["kind"], a: pending }, true)}
        </svg>
      )}

      {typing && (
        <input
          autoFocus
          value={draft}
          onChange={e => setDraft(e.target.value)}
          onBlur={() => { setTyping(null); onToolDone() }}
          onKeyDown={e => {
            if (e.key === "Enter" && draft.trim()) {
              commit({ id: nextId(), kind: "text", a: typing.at, text: draft.trim() })
              setTyping(null)
            } else if (e.key === "Escape") { setTyping(null); onToolDone() }
          }}
          placeholder="Label…"
          className="absolute z-20 w-[160px] rounded border border-accent bg-popover px-1.5 py-0.5 text-[12px] outline-none"
          style={{ left: typing.x, top: typing.y - 10 }}
        />
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
    { id: "rect", label: "Rectangle", glyph: "▭" },
    { id: "ellipse", label: "Ellipse", glyph: "◯" },
    { id: "fib", label: "Fibonacci retracement", glyph: "≡" },
    { id: "measure", label: "Measure", glyph: "⇕" },
    { id: "text", label: "Text label", glyph: "A" },
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
