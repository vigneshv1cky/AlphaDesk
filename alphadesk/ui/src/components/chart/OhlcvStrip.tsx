import { useMemo } from "react"
import type { ChartBar } from "@/lib/api"
import { Flash } from "@/components/terminal"

/** The O/H/L/C/V line above the chart, plus the card that follows the
 * crosshair. Both describe the same bar, so they live together — two
 * components reading one hover state is how they drift apart. */
export function OhlcvStrip({ bar, live, symbol, first, at }: {
  bar: ChartBar | null
  /** True when nothing is hovered and this is the latest bar. */
  live: boolean
  symbol: string
  first: ChartBar | null
  at: { x: number; y: number } | null
}) {
  const compact = useMemo(
    () => new Intl.NumberFormat("en-US", { notation: "compact", maximumFractionDigits: 1 }), [])
  if (!bar) return null
  const up = bar.c >= bar.o
  const tone = up ? "text-gain" : "text-loss"
  const stamp = new Date(bar.t).toLocaleString("en-US", {
    timeZone: "America/New_York", month: "short", day: "numeric",
    hour: "numeric", minute: "2-digit",
  })
  const base = first?.c
  const pct = base ? ((bar.c - base) / Math.abs(base)) * 100 : null

  /* `flash` is opt-in per cell and gated on `live` below, because this strip
     changes for TWO different reasons and only one of them is data. Moving the
     crosshair rewrites every cell, and flashing on a cursor move is precisely
     the noise the house rule exists to prevent. Only the live bar's close is a
     value that moved on its own. */
  const Cell = ({ k, v, flash }: { k: string; v: string; flash?: number }) => (
    <span className="whitespace-nowrap">
      <span className="text-muted-foreground">{k}</span>{" "}
      {flash == null
        ? <span className={`tnum ${tone}`}>{v}</span>
        : <Flash value={flash} className={`tnum ${tone}`}>{v}</Flash>}
    </span>
  )

  return (
    <>
      <div className="flex flex-wrap items-center gap-x-3 gap-y-0.5 px-1 pb-1 text-[12px]">
        <Cell k="O" v={bar.o.toFixed(2)} />
        <Cell k="H" v={bar.h.toFixed(2)} />
        <Cell k="L" v={bar.l.toFixed(2)} />
        <Cell k="C" v={bar.c.toFixed(2)} flash={live ? bar.c : undefined} />
        <Cell k="V" v={bar.v == null ? "—" : compact.format(bar.v)} />
        <span className="tnum text-[11px] text-muted-foreground">
          {stamp}{live ? " · latest" : ""}
        </span>
      </div>

      {/* The card, only while hovering. Change is measured from the first bar
          in the series — the move across what you are looking at. Previous
          close would be a different claim, and one this cannot check, since a
          range longer than a day has no single previous close. */}
      {!live && at && (
        <div
          className="pointer-events-none absolute z-20 rounded-md border border-border bg-popover/95 px-2.5 py-1.5 shadow-lg backdrop-blur"
          style={{ left: at.x + 14, top: Math.max(4, at.y) }}
        >
          <div className="text-[11px] text-muted-foreground">{stamp}</div>
          <div className="flex items-center gap-1.5 text-[12px]">
            <span className="h-1.5 w-1.5 rounded-full" style={{ background: "var(--accent)" }} />
            <span className="font-medium">{symbol}</span>
            <span className={`tnum ${pct == null ? "text-muted-foreground" : pct >= 0 ? "text-gain" : "text-loss"}`}>
              {pct == null ? "—" : `${pct >= 0 ? "+" : ""}${pct.toFixed(2)}%`}
            </span>
          </div>
        </div>
      )}
    </>
  )
}
