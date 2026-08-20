import { useEffect, useRef, useState } from "react"
import { OVERLAYS, type OverlayId } from "@/lib/indicators"
import type { SeriesKind } from "@/components/PriceChart"

/** The chart's controls: series type, range, scale, indicators, expand.
 *
 * Modelled on the toolbar their board carries (type · interval · Lin · Ind ·
 * Metrics) rather than invented. Two deliberate differences:
 *
 * Ranges stop at 1M because the series underneath is intraday bars from the
 * minute feed, which reaches ~30 days. A 1Y button would need a daily-bar path
 * and a second answer to whether the indicators can be trusted.
 *
 * There is no "Metrics" menu. It would need fundamentals plotted against
 * price, and this chart is fed by the bars endpoint alone — a menu that opened
 * onto nothing would be worse than its absence.
 */

const TYPES: { id: SeriesKind; label: string }[] = [
  { id: "candles", label: "Candles" },
  { id: "bars", label: "Bars" },
  { id: "line", label: "Line" },
  { id: "area", label: "Area" },
]

export const RANGES = [
  { days: 1, label: "1D" },
  { days: 2, label: "2D" },
  { days: 5, label: "5D" },
  { days: 10, label: "10D" },
  { days: 30, label: "1M" },
] as const

const btn = "px-2 py-[3px] text-[12px] transition-colors"
const on = "bg-accent/15 font-semibold text-accent"
const off = "text-muted-foreground hover:bg-muted hover:text-foreground"

/** A menu that closes on outside click and on Escape. Hand-rolled for the same
 * reason the rest of the primitives are — one popover does not justify a
 * dependency, and this one has to sit inside a 440px tile without clipping. */
function Menu({ label, active, children }: {
  label: string
  active?: boolean
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
        {label} <span className="text-[9px]">▾</span>
      </button>
      {open && (
        <div className="absolute left-0 top-full z-50 mt-1 min-w-[168px] rounded-md border border-border bg-popover py-1 shadow-lg">
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
  type, onType, days, onDays, logScale, onLogScale,
  overlays, onOverlays, panes, onPanes, expanded, onExpand, indicatorsReliable,
}: {
  type: SeriesKind
  onType: (t: SeriesKind) => void
  days: number
  onDays: (d: number) => void
  logScale: boolean
  onLogScale: (v: boolean) => void
  overlays: OverlayId[]
  onOverlays: (v: OverlayId[]) => void
  panes: boolean
  onPanes: (v: boolean) => void
  expanded: boolean
  onExpand: () => void
  indicatorsReliable: boolean
}) {
  const toggle = (id: OverlayId) =>
    onOverlays(overlays.includes(id) ? overlays.filter(o => o !== id) : [...overlays, id])

  return (
    <div className="flex flex-wrap items-center gap-1 border-b border-grid-line px-[12px] py-1">
      <Menu label={TYPES.find(t => t.id === type)!.label}>
        {close => TYPES.map(t => (
          <Item key={t.id} selected={t.id === type} onClick={() => { onType(t.id); close() }}>
            {t.label}
          </Item>
        ))}
      </Menu>

      <span className="mx-1 h-4 w-px bg-border" />

      {RANGES.map(r => (
        <button key={r.days} type="button" onClick={() => onDays(r.days)}
          className={`${btn} tabular-nums ${days === r.days ? on : off}`}>
          {r.label}
        </button>
      ))}

      <span className="mx-1 h-4 w-px bg-border" />

      <button type="button" onClick={() => onLogScale(!logScale)} className={`${btn} ${logScale ? on : off}`}>
        {logScale ? "Log" : "Lin"}
      </button>

      <Menu label="Ind" active={overlays.length > 0 || panes}>
        {() => (
          <>
            {OVERLAYS.map(o => (
              <Item key={o.id} selected={overlays.includes(o.id)} onClick={() => toggle(o.id)}>
                <span className="h-[2px] w-3 shrink-0" style={{ background: o.color }} />
                {o.label}
              </Item>
            ))}
            <div className="my-1 h-px bg-border" />
            <Item selected={panes} onClick={() => onPanes(!panes)}>
              RSI / MACD panes
            </Item>
            {!indicatorsReliable && (
              // The server measured this feed too sparse to support them, and
              // it hides them rather than draw something that looks right.
              <p className="px-3 py-1 text-[11px] leading-snug text-muted-foreground">
                Panes stay hidden — this feed is too sparse to compute them honestly.
              </p>
            )}
          </>
        )}
      </Menu>

      <div className="flex-1" />

      <button
        type="button"
        onClick={onExpand}
        aria-label={expanded ? "Collapse chart" : "Expand chart"}
        className={`${btn} ${off}`}
      >
        {expanded ? "⤡" : "⤢"}
      </button>
    </div>
  )
}
