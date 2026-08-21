import { useEffect, useMemo, useRef, useState } from "react"
import { OVERLAYS, PANE_INDICATORS, type OverlayId, type PaneId } from "@/lib/indicators"
import type { ScaleMode, SeriesKind } from "@/components/chart/ChartCanvas"
import type { ChartRange } from "@/lib/api"

/** The chart's controls: series type, interval, scale, indicators.
 *
 * Expanding is NOT here. It is a property of the tile rather than of the chart,
 * so it lives in the widget header alongside every other tile's — this toolbar
 * carried a second copy of it, which is one control too many for one action.
 *
 * Modelled on the toolbar their board carries (type · interval · Lin · Ind)
 * rather than invented.
 *
 * The interval readout is not a control. It reports which SERIES the range
 * selected — minute bars for 1D/5D, daily past that — because the reader
 * should be able to see that "3M" is not the same kind of data as "1D". The
 * server owns that mapping; offering it as a picker here would let the two
 * disagree.
 *
 * There is no "Metrics" menu. One existed — statements plotted in a pane under
 * price — and it was removed from the CHART on 2026-08-21. The server side is
 * deliberately still there: /api/fundamentals and ingest/prices.fundamentals_series
 * are untouched, so the data is a fetch away if the direction returns, and
 * `api.fundamentals` in lib/api.ts is still bound to it with no caller. Git
 * history has the menu, the pane and the wiring.
 */

const TYPES: { id: SeriesKind; label: string; glyph: string }[] = [
  { id: "candles", label: "Candlestick", glyph: "\u2547" },
  { id: "line", label: "Line", glyph: "\u2197" },
  { id: "bars", label: "Bar", glyph: "\u2540" },
  // Their name for a filled line. Kept because "Area" and "Mountain" describe
  // the same chart and theirs is the one a reader coming from it will look for.
  { id: "area", label: "Mountain", glyph: "\u25e2" },
]

/** Bar intervals. The server owns which are actually reachable for a given
 * range (ingest/prices.resolve_interval) and reports what it served, so this
 * list is what can be ASKED for rather than what will always be granted. */
export const INTERVALS: { id: string; label: string }[] = [
  { id: "1m", label: "1 min" }, { id: "2m", label: "2 mins" },
  { id: "5m", label: "5 mins" }, { id: "15m", label: "15 mins" },
  { id: "30m", label: "30 mins" }, { id: "1h", label: "1 hour" },
  { id: "4h", label: "4 hours" }, { id: "1d", label: "1 day" },
  { id: "1wk", label: "1 week" }, { id: "1mo", label: "1 month" },
]

const SCALES: { id: ScaleMode; label: string }[] = [
  { id: "linear", label: "Linear" },
  { id: "log", label: "Logarithmic" },
  { id: "percent", label: "Percent" },
]

/** Their exact set. 1D/5D are intraday, the rest daily — the server decides
 * which, so this is only a label. */
export const RANGES: ChartRange[] = ["1D", "5D", "1M", "3M", "6M", "YTD", "1Y", "5Y", "MAX"]

// Theirs are bordered pills, not bare text. That border is most of why their
// toolbar reads as a row of CONTROLS and ours read as a row of labels.
const btn = "rounded-md border px-2 py-[3px] text-[12px] transition-colors"
const on = "border-accent/40 bg-accent/15 font-semibold text-accent"
const off = "border-border text-muted-foreground hover:bg-muted hover:text-foreground"

/** A menu that closes on outside click and on Escape. Hand-rolled for the same
 * reason the rest of the primitives are — one popover does not justify a
 * dependency, and this one has to sit inside a 440px tile without clipping. */
function Menu({ label, active, wide, chevron = true, up = false, children }: {
  label: string
  active?: boolean
  wide?: boolean
  /** A value picker shows what it will change; a panel like "Ind" does not,
   * which is how theirs distinguishes the two. */
  chevron?: boolean
  /** Open upward. Required for anything on the BOTTOM edge of a tile: the
   * widget clips to its own box, so a menu dropped downward from there is
   * rendered entirely outside the clip and is simply invisible — it opens,
   * it responds, and nothing appears. */
  up?: boolean
  children: (close: () => void) => React.ReactNode
}) {
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (!open) return
    const away = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false)
    }
    const esc = (e: KeyboardEvent) => { if (e.key === "Escape") setOpen(false) }
    document.addEventListener("mousedown", away)
    document.addEventListener("keydown", esc)
    return () => {
      document.removeEventListener("mousedown", away)
      document.removeEventListener("keydown", esc)
    }
  }, [open])
  return (
    <div ref={ref} className="relative">
      <button
        type="button"
        aria-expanded={open}
        onClick={() => setOpen(o => !o)}
        className={`${btn} ${active || open ? on : off}`}
      >
        {label}{chevron && <span className="text-[9px]"> ▾</span>}
      </button>
      {open && (
        <div className={`absolute left-0 z-50 rounded-md border border-border bg-popover py-1 shadow-lg ${
          up ? "bottom-full mb-1" : "top-full mt-1"
        } ${wide ? "w-[320px]" : "min-w-[168px]"}`}>
          {children(() => setOpen(false))}
        </div>
      )}
    </div>
  )
}

function Item({ selected, onClick, children }: {
  selected?: boolean; onClick: () => void; children: React.ReactNode
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`flex w-full items-center gap-2 px-3 py-[5px] text-left text-[12px] hover:bg-muted ${
        selected ? "text-accent" : "text-foreground"
      }`}
    >
      <span className="w-3 shrink-0">{selected ? "✓" : ""}</span>
      {children}
    </button>
  )
}

export function ChartToolbar({
  type, onType, scale, onScale,
  overlays, onOverlays, panes, onPanes, indicatorsReliable,
  interval, onInterval, servedInterval, servedLabel, available, barCount = 0, drawOpen, onDrawOpen,
}: {
  type: SeriesKind
  onType: (t: SeriesKind) => void
  scale: ScaleMode
  onScale: (v: ScaleMode) => void
  interval: string
  onInterval: (v: string) => void
  /** What the server actually served — may be coarser than asked. */
  servedInterval?: string
  servedLabel?: string
  /** The interval ids this range may offer, from the server. Undefined before
   * the first response, when the full list is the honest thing to show. */
  available?: string[]
  overlays: OverlayId[]
  onOverlays: (v: OverlayId[]) => void
  panes: PaneId[]
  onPanes: (v: PaneId[]) => void
  drawOpen: boolean
  onDrawOpen: () => void
  indicatorsReliable: boolean
  /** Bars on this range, so an indicator that cannot warm up on them is not
   * offered as though it could. */
  barCount?: number
}) {
  const toggle = (id: OverlayId) =>
    onOverlays(overlays.includes(id) ? overlays.filter(o => o !== id) : [...overlays, id])

  return (
    <div className="flex flex-wrap items-center gap-1 border-b border-grid-line px-[12px] py-1">
      {/* A glyph rather than the word, matching theirs — the series type is the
          one control whose current value is legible at a glance as a picture,
          and spelling out "Candlestick" costs the row most of its width. */}
      <Menu label={TYPES.find(t => t.id === type)!.glyph}>
        {close => TYPES.map(t => (
          <Item key={t.id} selected={t.id === type} onClick={() => { onType(t.id); close() }}>
            {t.label}
          </Item>
        ))}
      </Menu>

      <Menu label={INTERVALS.find(i => i.id === interval)?.label ?? interval}
            active={!!servedInterval && servedInterval !== interval}>
        {close => (
          <>
            {INTERVALS.filter(i => !available || available.includes(i.id)).map(i => (
              <Item key={i.id} selected={i.id === interval} onClick={() => { onInterval(i.id); close() }}>
                {i.label}
              </Item>
            ))}
            {servedInterval && servedInterval !== interval && (
              // Say so rather than silently redrawing at a coarser interval —
              // a chart that quietly changes what a bar means is the same
              // failure as an indicator drawn on data too sparse for it.
              <p className="px-3 py-1 text-[11px] leading-snug text-muted-foreground">
                This range cannot reach that interval; showing {servedLabel} instead.
              </p>
            )}
          </>
        )}
      </Menu>

      <Menu label={SCALES.find(x => x.id === scale)!.label.slice(0, 3)}>
        {close => SCALES.map(x => (
          <Item key={x.id} selected={x.id === scale} onClick={() => { onScale(x.id); close() }}>
            {x.label}
          </Item>
        ))}
      </Menu>

      <Menu label="Ind" active={overlays.length > 0 || panes.length > 0} wide chevron={false}>
        {() => (
          <IndicatorMenu
            overlays={overlays} toggle={toggle}
            panes={panes} onPanes={onPanes}
            indicatorsReliable={indicatorsReliable}
            barCount={barCount}
          />
        )}
      </Menu>

      <div className="flex-1" />

      <button
        type="button"
        onClick={onDrawOpen}
        aria-label="Drawing tools"
        title="Drawing tools"
        className={`${btn} ${drawOpen ? on : off}`}
      >
        ✎
      </button>
      {/* No expand button here. Expanding is a WIDGET action, not a chart one,
          and it now lives in the widget header where every other tile carries
          it — one control in one place beats the same control twice. */}
    </div>
  )
}

/** The range strip. Theirs runs along the BOTTOM of the chart, under the time
 * axis, which is where a reader reaches for it — the top toolbar is for what
 * the chart IS, the bottom for how much of it you are looking at. */
/** The three theirs keeps on the strip. The rest live behind the chevron —
 * spelling out all nine turned the strip into a second toolbar. */
const QUICK_RANGES: ChartRange[] = ["1D", "1M", "1Y"]

export function ChartRanges({ range, onRange }: {
  range: ChartRange
  onRange: (r: ChartRange) => void
}) {
  // The active range is ALWAYS on the strip, even when it is not one of the
  // three. Hiding the selection inside a closed menu would leave the strip
  // showing nothing selected while the chart plainly is.
  const shown = QUICK_RANGES.includes(range) ? QUICK_RANGES : [...QUICK_RANGES, range]
  return (
    <div className="flex flex-wrap items-center gap-1 border-t border-grid-line px-[12px] py-1">
      {shown.map(r => (
        <button
          key={r}
          type="button"
          onClick={() => onRange(r)}
          className={`rounded-md px-2 py-[3px] text-[12px] tabular-nums transition-colors ${
            range === r ? "bg-accent/15 font-semibold text-accent"
                        : "text-muted-foreground hover:bg-muted hover:text-foreground"
          }`}
        >
          {r}
        </button>
      ))}
      {/* Every range is in here, including the three above — a reader who opens
          it looking for "1D" should find it rather than be told to look back
          out at the strip. */}
      <Menu label="" chevron up>
        {close => RANGES.map(r => (
          <Item key={r} selected={r === range} onClick={() => { onRange(r); close() }}>
            {r}
          </Item>
        ))}
      </Menu>
    </div>
  )
}

/** The indicator picker: a search box over a grouped list, the way theirs is.
 * With a dozen entries a flat list is already hard to scan, and the groups are
 * how a reader thinks about them — averages, then bands, then volume. */
function IndicatorMenu({ overlays, toggle, panes, onPanes, indicatorsReliable, barCount }: {
  overlays: OverlayId[]
  toggle: (id: OverlayId) => void
  panes: PaneId[]
  onPanes: (v: PaneId[]) => void
  indicatorsReliable: boolean
  barCount: number
}) {
  const [q, setQ] = useState("")
  const needle = q.trim().toLowerCase()
  const groups = useMemo(() => {
    const hits = OVERLAYS.filter(o => !needle || o.label.toLowerCase().includes(needle))
    const by = new Map<string, typeof OVERLAYS>()
    for (const o of hits) by.set(o.group, [...(by.get(o.group) ?? []), o])
    return [...by.entries()]
  }, [needle])

  /** Pane indicators are grouped the same way and searched by the same box —
   * a reader looking for "stochastic" should not have to know that it draws
   * under the price rather than over it. */
  const paneGroups = useMemo(() => {
    // Only what this range has the bars to warm up. Offering MACD on a 24-bar
    // month would tick a box and draw nothing.
    const hits = PANE_INDICATORS.filter(o =>
      (!needle || o.label.toLowerCase().includes(needle)) && barCount >= o.minBars)
    const by = new Map<string, typeof PANE_INDICATORS>()
    for (const o of hits) by.set(o.group, [...(by.get(o.group) ?? []), o])
    return [...by.entries()]
  }, [needle, barCount])

  const togglePane = (id: PaneId) =>
    onPanes(panes.includes(id) ? panes.filter(p => p !== id) : [...panes, id])

  return (
    <>
      <input
        autoFocus
        value={q}
        onChange={e => setQ(e.target.value)}
        placeholder="Search indicators…"
        aria-label="Search indicators"
        className="mb-1 w-full border-b border-grid-line bg-transparent px-3 py-2 text-[13px] outline-none placeholder:text-muted-foreground"
      />
      <div className="max-h-[280px] overflow-y-auto">
        {groups.length === 0 && paneGroups.length === 0 && (
          <p className="px-3 py-3 text-[12px] text-muted-foreground">No indicator matches “{q}”.</p>
        )}
        {groups.map(([group, items]) => (
          <div key={group}>
            <div className="bg-panel-header px-3 py-1 text-[10px] font-medium uppercase tracking-[1px] text-muted-foreground">
              {group}
            </div>
            {items.map(o => (
              <Item key={o.id} selected={overlays.includes(o.id)} onClick={() => toggle(o.id)}>
                <span className="h-[2px] w-3 shrink-0" style={{ background: o.color }} />
                <span className="truncate">{o.label}</span>
              </Item>
            ))}
          </div>
        ))}
        {!indicatorsReliable && paneGroups.length > 0 && (
          // The server measured this feed too sparse to support them, and it
          // hides them rather than draw something that looks right. That
          // verdict covers the browser-computed oscillators too: they read the
          // same bars, so they inherit the same doubt.
          <>
            <div className="bg-panel-header px-3 py-1 text-[10px] font-medium uppercase tracking-[1px] text-muted-foreground">
              Panes
            </div>
            <p className="px-3 py-1 text-[11px] leading-snug text-muted-foreground">
              Hidden for this symbol — the feed is too sparse to compute them honestly.
            </p>
          </>
        )}
        {indicatorsReliable && paneGroups.map(([group, items]) => (
          <div key={group}>
            <div className="bg-panel-header px-3 py-1 text-[10px] font-medium uppercase tracking-[1px] text-muted-foreground">
              {group}
            </div>
            {items.map(o => (
              <Item key={o.id} selected={panes.includes(o.id)} onClick={() => togglePane(o.id)}>
                <span className="truncate">{o.label}</span>
              </Item>
            ))}
          </div>
        ))}
      </div>
    </>
  )
}
