import { useEffect, useState, type ReactNode } from "react"
import { ChevronDown, Search } from "lucide-react"
import { type EarningsRow } from "@/lib/api"
import { InfoTip } from "@/components/InfoTip"
import { Badge, Button, Empty, Table, TableBody, TableCell, TableHead, TableHeader, TableRow, Widget, fieldCls } from "@/components/terminal"

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

// Did the desk act on a reporter? (coverage self-assessment)
const ENG: Record<string, { label: string; cls: string }> = {
  TOOK: { label: "Took", cls: "bg-emerald-500/15 text-emerald-600 dark:text-emerald-400" },
  DEBATED: { label: "Debated", cls: "bg-indigo-500/15 text-indigo-600 dark:text-indigo-400" },
  SKIPPED: { label: "Skipped", cls: "bg-amber-500/15 text-amber-600 dark:text-amber-400" },
}

function EngBadge({ state }: { state?: string }) {
  const b = state ? ENG[state] : undefined
  if (!b) return <Badge variant="ghost" className="text-muted-foreground/40">·</Badge>
  return <Badge className={b.cls}>{b.label}</Badge>
}

const BIG_MOVE = 6 // % drift that counts as a real move (matches the skip-miss line)
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

// Classify a reporter's outcome vs what the desk did. A big drift the desk didn't
// act on is only a TRUE miss if it was tradeable; in a thin/illiquid name it's a
// FALSE miss (a pump you couldn't have captured — the HIHO case). For names the
// desk DID act on, whether the interim drift is going its way (not the official
// grade, which settles at the horizon).
function assess(e: EarningsRow): { label: string; cls: string; tip: string } | null {
  // Key on the CAPTURABLE drift (from the open), not the gap-inclusive total — the
  // overnight gap repriced before you could act, so a pure-gap move is not a miss.
  const move = e.move_drift_pct ?? e.move_since_report_pct
  if (move == null) return { label: "pending", cls: "text-muted-foreground/50", tip: "no post-report session yet" }
  const eng = e.engagement
  if (eng === "TOOK" || eng === "DEBATED") {
    if (!e.engagement_dir || Math.abs(move) < 1)
      return { label: "flat", cls: "text-muted-foreground/60", tip: "little drift so far" }
    const favorable = e.engagement_dir === "LONG" ? move > 0 : move < 0
    return favorable
      ? { label: "on track", cls: "text-gain", tip: "interim drift is going our way (not the official grade)" }
      : { label: "adverse", cls: "text-loss", tip: "interim drift is against our call (not the official grade)" }
  }
  // SKIPPED / UNSEEN
  if (Math.abs(move) < BIG_MOVE)
    return { label: "fair pass", cls: "text-muted-foreground/60", tip: "small move — nothing forgone" }
  const thin = isLowLiquidity(e)
  return thin
    ? { label: "false miss", cls: "text-amber-600 dark:text-amber-400", tip: "big move but too illiquid to trade at size — uncatchable" }
    : { label: "true miss", cls: "font-semibold text-red-600 dark:text-red-400", tip: "big, tradeable move the desk didn't act on" }
}

function AssessTag({ e }: { e: EarningsRow }) {
  const a = assess(e)
  if (!a) return null
  return (
    <InfoTip tip={a.tip} className={`cursor-help text-[10px] ${a.cls}`}>
      {a.label}
    </InfoTip>
  )
}

// One-glance "did we do well?" — how many reporters the desk took / debated /
// skipped / never saw, plus the biggest drift it didn't act on.
function CoverageSummary({ reported }: { reported: EarningsRow[] }) {
  const c = (s: string) => reported.filter((e) => e.engagement === s).length
  const took = c("TOOK")
  const debated = c("DEBATED")
  const skipped = c("SKIPPED")
  const unseen = reported.length - took - debated - skipped
  const trueMiss = reported.filter((e) => assess(e)?.label === "true miss").length
  const falseMiss = reported.filter((e) => assess(e)?.label === "false miss").length
  const capturable = (e: EarningsRow) => e.move_drift_pct ?? e.move_since_report_pct ?? 0
  const worst = reported
    .filter((e) => assess(e)?.label === "true miss")
    .sort((a, b) => Math.abs(capturable(b)) - Math.abs(capturable(a)))[0]
  return (
    <div className="mb-2 bg-muted/40 px-2.5 py-2 text-[11px]">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
        <span className="font-medium text-muted-foreground">Desk coverage</span>
        <span className="text-emerald-600 dark:text-emerald-400">{took} took</span>
        <span className="text-indigo-600 dark:text-indigo-400">{debated} debated</span>
        <span className="text-amber-600 dark:text-amber-400">{skipped} skipped</span>
        <span className="text-muted-foreground">{unseen} not seen</span>
      </div>
      <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1">
        <span className={trueMiss > 0 ? "font-semibold text-red-600 dark:text-red-400" : "text-muted-foreground"}>
          {trueMiss} true miss{trueMiss === 1 ? "" : "es"}
        </span>
        <span className="text-amber-600 dark:text-amber-400">{falseMiss} false (untradeable)</span>
        {worst && (
          <span className="text-muted-foreground">
            worst: <span className="font-semibold text-foreground">{worst.symbol}</span>{" "}
            <span className={capturable(worst) >= 0 ? "text-gain" : "text-loss"}>
              {capturable(worst) >= 0 ? "+" : ""}
              {capturable(worst).toFixed(1)}% drift
            </span>
          </span>
        )}
      </div>
    </div>
  )
}

function whyText(e: EarningsRow): string {
  if (e.engagement === "UNSEEN")
    return "Not surfaced — the desk didn't run after this reported, or it wasn't in that run's news/earnings window."
  return e.engagement_why || "(no reason recorded)"
}

// A reporter row that expands to show WHY the desk acted as it did (its own
// stored reasoning: judge summary / thesis for takes & debates, the scout's
// reason for skips, or the coverage-gap note for unseen). Two <TableRow>s per
// reporter, not one <tr> with a nested toggle button — same reasoning as
// PerformancePage's TradeRow: <button> isn't valid inside <tr>.
function ReportedRow({ e }: { e: EarningsRow }) {
  const [open, setOpen] = useState(false)
  // Headline the CAPTURABLE drift (from the open); show the uncapturable gap as
  // muted context so a pure-gap reprice reads as "gap, no drift", not a big move.
  const drift = e.move_drift_pct ?? e.move_since_report_pct
  const gap = e.move_gap_pct
  const has = drift != null
  const up = (drift ?? 0) >= 0
  const took = e.engagement === "TOOK" || e.engagement === "DEBATED"
  return (
    <>
      <TableRow onClick={() => setOpen((v) => !v)} aria-expanded={open} className="cursor-pointer">
        <TableCell className="font-semibold">{e.symbol}</TableCell>
        <TableCell className="text-xs text-muted-foreground">{fmtCap(e.market_cap)}</TableCell>
        <TableCell className="text-xs text-muted-foreground">{e.session}</TableCell>
        <TableCell><EngBadge state={e.engagement} /></TableCell>
        <TableCell><AssessTag e={e} /></TableCell>
        <TableCell className="text-right font-mono text-xs tabular-nums">
          {has ? (
            <>
              <InfoTip
                tip="Capturable drift since the report — the move from the first post-report open (excludes the uncapturable overnight gap)"
                className={`cursor-help ${up ? "text-gain" : "text-loss"}`}
              >
                {up ? "+" : ""}
                {drift!.toFixed(1)}%
              </InfoTip>
              {gap != null && Math.abs(gap) >= 0.1 && (
                <span className="ml-1.5 text-muted-foreground/50">
                  {gap >= 0 ? "+" : ""}
                  {gap.toFixed(1)}% gap
                </span>
              )}
            </>
          ) : (
            <span className="text-muted-foreground">—</span>
          )}
        </TableCell>
        <TableCell>
          <ChevronDown
            className={`h-3.5 w-3.5 shrink-0 text-muted-foreground/40 transition-transform ${
              open ? "" : "-rotate-90"
            }`}
          />
        </TableCell>
      </TableRow>
      {open && (
        <TableRow className="hover:bg-transparent">
          <TableCell colSpan={7} className="bg-muted/40">
            <div className="py-1 text-xs leading-relaxed text-muted-foreground">
              {took && e.engagement_dir && (
                <span className="mr-1 font-medium text-foreground">
                  {e.engagement_dir === "LONG" ? "Long" : "Short"}
                  {e.engagement_verdict ? ` · ${e.engagement_verdict}` : ""}:
                </span>
              )}
              {whyText(e)}
            </div>
          </TableCell>
        </TableRow>
      )}
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
          <div className="max-h-[420px] overflow-y-auto">
          <Table>
            <TableHeader><TableRow>
              <TableHead>Symbol</TableHead>
              <TableHead>Cap</TableHead>
              <TableHead>Session</TableHead>
              <TableHead>Desk</TableHead>
              <TableHead>Verdict</TableHead>
              <TableHead className="text-right">Drift · gap</TableHead>
              <TableHead />
            </TableRow></TableHeader>
            <TableBody>
              {g.rows.map((e) => (
                <ReportedRow key={e.symbol + e.report_date} e={e} />
              ))}
            </TableBody>
          </Table>
          </div>
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
          sub="capturable drift from the first post-report open — the uncapturable overnight gap is shown separately and excluded from the verdict"
        >
          <CoverageSummary reported={filteredReported} />
          <div className="space-y-1">
            {groupByDay(filteredReported, (e) => e.report_date.slice(0, 10)).map((g) => (
              <ReportedDayBlock key={g.day} g={g} forceExpanded={searching} />
            ))}
          </div>
        </Panel>
      )}

      {filteredUpcoming.length > 0 && (
        <Panel title="Reporting soon" sub="grouped by when to run the desk — biggest names first">
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
