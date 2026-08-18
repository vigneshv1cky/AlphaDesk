import { useEffect, useRef, useState, type ReactNode } from "react"
import { ChevronDown, Search } from "lucide-react"
import { type EarningsRow } from "@/lib/api"
import { useVirtualizer } from "@tanstack/react-virtual"
import { Badge, Button, Empty, Table, TableBody, TableCell, TableHead, TableHeader, TableRow, Widget, fieldCls } from "@/components/terminal"
import { Link } from "react-router-dom"

function Panel({
  title,
  sub,
  children,
  collapsible = false,
  defaultOpen = true,
  count,
}: {
  title?: string
  sub?: string
  children: ReactNode
  collapsible?: boolean
  defaultOpen?: boolean
  count?: number
}) {
  const [open, setOpen] = useState(defaultOpen)
  // defaultOpen can flip true later (e.g. a search starts matching) — force the
  // panel open when that happens, but don't fight a manual close afterward.
  useEffect(() => {
    if (defaultOpen) setOpen(true)
  }, [defaultOpen])
  if (collapsible && title) {
    // controlled Collapsible: keeps the exact chevron/count/sub look while adding
    // the animated height reveal + aria-expanded/controls for free.
    return (
      // Plain button + conditional render, replacing the Base UI Collapsible.
      // The animated height reveal it provided is not worth a dependency here:
      // these sections hold long tables, and animating their height on every
      // toggle is exactly the motion a dense grid should not have.
      <div className="border border-border bg-panel">
        <button
          type="button"
          onClick={() => setOpen(o => !o)}
          aria-expanded={open}
          className="group flex h-[26px] w-full items-center gap-2 border-b border-border bg-panel-header px-2 text-left hover:bg-muted"
        >
          <span className="text-[10px] font-semibold uppercase tracking-[0.08em] text-foreground/80">
            {title}
          </span>
          {count != null && <Badge variant="secondary">{count}</Badge>}
          <ChevronDown
            className={`ml-auto h-3 w-3 shrink-0 text-muted-foreground transition-transform group-hover:text-foreground ${
              open ? "" : "-rotate-90"
            }`}
          />
        </button>
        {open && (
          <>
            {sub && <div className="px-2 py-1 text-[10px] text-muted-foreground">{sub}</div>}
            {children}
          </>
        )}
      </div>
    )
  }
  return (
    <Widget title={title} subtitle={sub}>
      {children}
    </Widget>
  )
}

function fmtCap(v?: number | null): string {
  if (v == null) return ""
  if (v >= 1e12) return `$${(v / 1e12).toFixed(1)}T`
  if (v >= 1e9) return `$${(v / 1e9).toFixed(0)}B`
  if (v >= 1e6) return `$${(v / 1e6).toFixed(0)}M`
  return `$${Math.round(v)}`
}

function dayLabel(day: string): string {
  const d = new Date(`${day}T12:00:00`) // noon avoids TZ date-rollover
  const wd = d.toLocaleDateString("en-US", { weekday: "short" })
  return `${wd} ${day.slice(5)}`
}

type DayGroup = { day: string; rows: EarningsRow[] }

// Rows arrive pre-sorted, so a single pass yields contiguous day-groups (biggest
// names first inside each). `key` picks the grouping day: run-day for upcoming,
// report-day for just-reported.
function groupByDay(rows: EarningsRow[], key: (e: EarningsRow) => string): DayGroup[] {
  const groups: DayGroup[] = []
  for (const e of rows) {
    const day = key(e)
    let g = groups[groups.length - 1]
    if (!g || g.day !== day) {
      g = { day, rows: [] }
      groups.push(g)
    }
    g.rows.push(e)
  }
  return groups
}

// The desk-coverage widgets that used to live here (EngBadge, assess,
// AssessTag, CoverageSummary, whyText) are gone. Every one of them answered
// "how well did the trading desk do against this reporter" — a measurement
// question, from tables that no longer exist.

const THIN_CAP = 100_000_000 // below ~$100M cap: effectively untradeable at size

// Prefer the real 20d-avg-$vol flag (same bar the trading pipeline gates entries
// on); fall back to the cap heuristic when liquidity couldn't be measured at
// all. That fallback matters: the most obscure penny stocks are both the ones
// most likely to fail a volume lookup AND the ones most likely to actually be
// illiquid, so "unmeasurable" should lean toward treating it as thin, not
// toward showing it as if it were confirmed liquid.
function isLowLiquidity(e: EarningsRow): boolean {
  return e.low_liquidity ?? ((e.market_cap ?? Infinity) < THIN_CAP)
}


// One reported name.
//
// Flex divs, NOT <tr>s. This block is virtualized (see ReportedTable) and the
// largest reported day carries 289 names; a virtualizer positions each item
// absolutely, which a <tbody> cannot express. Rendering summary + detail
// inside ONE element also makes each reporter a single virtual item whose
// height simply changes when it opens, instead of two sibling rows the
// virtualizer would have to keep in sync.
// Symbol · cap · session · actual · estimate · chart-link. 0 = flex-fill.
const RCOLS = [80, 70, 60, 0, 0, 70] as const
const ROW_H = 25        // every row, including its 1px rule — uniform now that
                        // rows no longer expand, so the virtualizer needs no
                        // measurement at all.

function rcol(i: number) {
  const w = RCOLS[i]
  return w ? { width: w, flex: "0 0 auto" as const } : { flex: "1 1 0%" as const, minWidth: 0 }
}

function ReportedRow({ e }: { e: EarningsRow }) {
  // Rows no longer expand. The detail panel showed the desk's own reasoning
  // for engaging with a reporter, and the drift column showed how the name
  // moved afterwards — both came from tables the retired trading engine wrote
  // and nothing populates now. A calendar answers "who reported when"; what
  // the name did next belongs on its chart.
  return (
    <div className="flex h-[24px] items-center border-b border-grid-line text-[11px] hover:bg-muted/60">
      <div style={rcol(0)} className="truncate px-2 font-semibold">{e.symbol}</div>
      <div style={rcol(1)} className="truncate px-2 text-muted-foreground">{fmtCap(e.market_cap)}</div>
      <div style={rcol(2)} className="truncate px-2 text-muted-foreground">{e.session}</div>
      <div style={rcol(3)} className="num truncate px-2 text-right text-muted-foreground">
        {e.eps_actual ?? "—"}
      </div>
      <div style={rcol(4)} className="num truncate px-2 text-right text-muted-foreground">
        {e.eps_estimate ?? "—"}
      </div>
      <div style={rcol(5)} className="px-2 text-right">
        <Link to={`/chart?symbol=${encodeURIComponent(e.symbol)}`} className="text-accent hover:underline">
          chart →
        </Link>
      </div>
    </div>
  )
}

/** The reported-day table, virtualized.
 *
 * Measured live: the largest reported day is 289 names, which previously
 * mounted ~1,700 DOM nodes and pushed ~7,000px of page.
 *
 * Rows are a uniform height now that they no longer expand, so the virtualizer
 * needs no measurement — which also sidesteps a real problem: react-virtual's
 * `measureElement` ResizeObserver never fired for the old expanding rows
 * (verified at 289-row scale — the row grew while the spacer and following
 * offsets did not, so an expanded row sat on top of its neighbour). */
function ReportedTable({ rows }: { rows: EarningsRow[] }) {
  const scrollRef = useRef<HTMLDivElement>(null)
  const virt = useVirtualizer({
    count: rows.length,
    getScrollElement: () => scrollRef.current,
    estimateSize: () => ROW_H,
    overscan: 10,
  })
  const HEADS = ["Symbol", "Cap", "Session", "Actual", "Est", ""]
  return (
    <>
      <div className="flex border-b border-border bg-panel-header">
        {HEADS.map((h, i) => (
          <div
            key={h || i}
            style={rcol(i)}
            className={`h-[22px] truncate px-2 text-[9px] font-semibold uppercase leading-[22px] tracking-[0.06em] text-muted-foreground ${
              i >= 3 ? "text-right" : ""
            }`}
          >
            {h}
          </div>
        ))}
      </div>
      <div ref={scrollRef} className="max-h-[420px] overflow-y-auto">
        <div style={{ height: virt.getTotalSize(), position: "relative" }}>
          {virt.getVirtualItems().map((vi) => (
            <div
              key={rows[vi.index].symbol + rows[vi.index].report_date}
              style={{ position: "absolute", top: 0, left: 0, width: "100%", height: vi.size, transform: `translateY(${vi.start}px)` }}
            >
              <ReportedRow e={rows[vi.index]} />
            </div>
          ))}
        </div>
      </div>
    </>
  )
}

// One run-day group in "Reporting soon". The day header itself collapses the
// whole block — with a dozen-plus run-days on the calendar, having every one
// fully expanded meant scrolling past every earlier day just to reach a later
// one. Only the nearest day is open by default; the rest are one click away.
// Once a day is open, it shows every name in it — "show less" at the bottom
// re-collapses the day without scrolling back up to the header.
function RunGroup({
  g,
  defaultOpen = false,
  forceExpanded,
}: {
  g: DayGroup
  defaultOpen?: boolean
  forceExpanded?: boolean
}) {
  const [open, setOpen] = useState(defaultOpen || !!forceExpanded)
  useEffect(() => {
    if (forceExpanded) setOpen(true)
  }, [forceExpanded])
  return (
    <div>
      <Button
        variant="ghost"
        onClick={() => setOpen((v) => !v)}
        className="group -mx-1 flex h-auto w-full items-center gap-2 px-1 py-1 text-left"
      >
        <span className="text-xs font-semibold text-emerald-600 dark:text-emerald-400">
          {g.day === "—" ? "Run time n/a" : `Run ${dayLabel(g.day)} · 9:30 ET`}
        </span>
        <span className="text-[11px] text-muted-foreground">{g.rows.length} names</span>
        <ChevronDown
          className={`ml-auto h-3.5 w-3.5 shrink-0 text-muted-foreground/50 transition-transform group-hover:text-foreground ${
            open ? "" : "-rotate-90"
          }`}
        />
      </Button>
      {open && (
        <>
          <div className="max-h-[420px] overflow-y-auto">
          <Table>
            <TableHeader><TableRow>
              <TableHead>Symbol</TableHead>
              <TableHead>Cap</TableHead>
              <TableHead className="text-right">Report</TableHead>
            </TableRow></TableHeader>
            <TableBody>
              {g.rows.map((e) => (
                <TableRow key={e.symbol + e.report_date}>
                  <TableCell className="font-semibold">{e.symbol}</TableCell>
                  <TableCell className="text-xs text-muted-foreground">{fmtCap(e.market_cap)}</TableCell>
                  <TableCell className="text-right text-xs text-muted-foreground">
                    {e.report_date.slice(5, 10)} {e.session}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
          </div>
          <Button
            variant="ghost"
            onClick={() => setOpen(false)}
            className="mt-1 font-medium"
          >
            − show less
          </Button>
        </>
      )}
    </div>
  )
}

// One reported day-block — same collapsible-day-header treatment as RunGroup
// in "Reporting soon". "Just reported" is almost always a single day in
// practice, but this keeps the interaction consistent and ready if that
// window ever widens. Once open, shows every name — "show less" at the
// bottom re-collapses the day without scrolling back up to the header.
function ReportedDayBlock({
  g,
  defaultOpen = false,
  forceExpanded,
}: {
  g: DayGroup
  defaultOpen?: boolean
  forceExpanded?: boolean
}) {
  const [open, setOpen] = useState(defaultOpen || !!forceExpanded)
  useEffect(() => {
    if (forceExpanded) setOpen(true)
  }, [forceExpanded])
  return (
    <div>
      <Button
        variant="ghost"
        onClick={() => setOpen((v) => !v)}
        className="group -mx-1 flex h-auto w-full items-center gap-2 px-1 py-1 text-left"
      >
        <span className="text-xs font-semibold text-indigo-600 dark:text-indigo-400">
          Reported {dayLabel(g.day)}
        </span>
        <span className="text-[11px] text-muted-foreground">{g.rows.length} names</span>
        <ChevronDown
          className={`ml-auto h-3.5 w-3.5 shrink-0 text-muted-foreground/50 transition-transform group-hover:text-foreground ${
            open ? "" : "-rotate-90"
          }`}
        />
      </Button>
      {open && (
        <>
          <ReportedTable rows={g.rows} />
          <div className="py-2 text-center">
            <Button variant="ghost" onClick={() => setOpen(false)} className="text-xs">
              − show less
            </Button>
          </div>
        </>
      )}
    </div>
  )
}

export function Earnings({
  earnings,
}: {
  earnings?: { upcoming: EarningsRow[]; reported: EarningsRow[] } | null
}) {
  const [query, setQuery] = useState("")
  if (earnings === null || earnings === undefined) {
    return (
      <Panel>
        <div className="flex items-center gap-3 py-3">
          <div className="h-4 w-4 animate-spin rounded-full border-2 border-indigo-500 border-t-transparent" />
          <p className="text-[11px] text-muted-foreground">Loading earnings calendar…</p>
        </div>
      </Panel>
    )
  }
  if (earnings.reported.length === 0 && earnings.upcoming.length === 0) {
    return (
      <Panel>
        <p className="text-[11px] text-muted-foreground">
          No earnings on the calendar yet — it refreshes a few times a day.
        </p>
      </Panel>
    )
  }

  // Same 20d-avg-$vol bar the trading pipeline actually gates entries on — don't
  // even list a name the desk would refuse to trade regardless of how it moved.
  const tradeableReported = earnings.reported.filter((e) => !isLowLiquidity(e))
  const tradeableUpcoming = earnings.upcoming.filter((e) => !isLowLiquidity(e))

  const q = query.trim().toUpperCase()
  const searching = q.length > 0
  const filteredReported = searching
    ? tradeableReported.filter((e) => e.symbol.toUpperCase().includes(q))
    : tradeableReported
  const filteredUpcoming = searching
    ? tradeableUpcoming.filter((e) => e.symbol.toUpperCase().includes(q))
    : tradeableUpcoming
  const noMatches = searching && filteredReported.length === 0 && filteredUpcoming.length === 0

  return (
    <div>
      <div className="relative border-b border-border p-1">
        <Search className="pointer-events-none absolute left-2.5 top-1/2 h-3 w-3 -translate-y-1/2 text-muted-foreground" />
        <input
          type="text"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search by symbol…"
          className={`${fieldCls} w-full pl-6 sm:w-64`}
        />
      </div>

      {noMatches && (
        <Empty>No matches for "{query.trim()}".</Empty>
      )}

      {filteredReported.length > 0 && (
        <Panel
          title="Just reported"
          sub="most recent first — open a day to see the names and what they printed"
        >
          <div className="space-y-1">
            {groupByDay(filteredReported, (e) => e.report_date.slice(0, 10)).map((g) => (
              <ReportedDayBlock key={g.day} g={g} forceExpanded={searching} />
            ))}
          </div>
        </Panel>
      )}

      {filteredUpcoming.length > 0 && (
        <Panel title="Reporting soon" sub="grouped by report day — biggest names first">
          <div className="space-y-1">
            {groupByDay(filteredUpcoming, (e) => (e.run_at ?? "").slice(0, 10) || "—").map((g) => (
              <RunGroup key={g.day} g={g} forceExpanded={searching} />
            ))}
          </div>
        </Panel>
      )}
    </div>
  )
}
