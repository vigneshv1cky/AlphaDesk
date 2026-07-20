import type { ReactNode } from "react"
import type { EarningsRow } from "@/lib/api"

function Panel({ title, sub, children }: { title?: string; sub?: string; children: ReactNode }) {
  return (
    <div className="rounded-lg border border-border bg-card p-4">
      {title && (
        <div className="mb-2">
          <div className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
            {title}
          </div>
          {sub && <div className="text-[11px] text-muted-foreground">{sub}</div>}
        </div>
      )}
      {children}
    </div>
  )
}

function fmtCap(v?: number | null): string {
  if (v == null) return ""
  if (v >= 1e12) return `$${(v / 1e12).toFixed(1)}T`
  if (v >= 1e9) return `$${(v / 1e9).toFixed(0)}B`
  if (v >= 1e6) return `$${(v / 1e6).toFixed(0)}M`
  return `$${Math.round(v)}`
}

function runDayLabel(runDay: string): string {
  const d = new Date(`${runDay}T12:00:00`) // noon avoids TZ date-rollover
  const wd = d.toLocaleDateString("en-US", { weekday: "short" })
  return `${wd} ${runDay.slice(5)}`
}

type RunGroup = { runDay: string; rows: EarningsRow[] }

// upcoming arrives pre-sorted by (run_at asc, market_cap desc), so a single pass
// yields run-day groups with the biggest names first inside each.
function groupByRunDay(rows: EarningsRow[]): RunGroup[] {
  const groups: RunGroup[] = []
  for (const e of rows) {
    const runDay = (e.run_at ?? "").slice(0, 10) || "—"
    let g = groups[groups.length - 1]
    if (!g || g.runDay !== runDay) {
      g = { runDay, rows: [] }
      groups.push(g)
    }
    g.rows.push(e)
  }
  return groups
}

export function Earnings({
  earnings,
}: {
  earnings?: { upcoming: EarningsRow[]; reported: EarningsRow[] }
}) {
  if (!earnings || (earnings.reported.length === 0 && earnings.upcoming.length === 0)) {
    return (
      <Panel>
        <p className="text-sm text-muted-foreground">
          No earnings on the calendar yet — it refreshes a few times a day.
        </p>
      </Panel>
    )
  }

  return (
    <div className="space-y-3">
      {earnings.reported.length > 0 && (
        <Panel title="Just reported" sub="move since the report — the drift so far">
          <div className="flex flex-wrap gap-2">
            {earnings.reported.map((e) => {
              const move = e.move_since_report_pct
              const has = move != null
              const up = (move ?? 0) >= 0
              return (
                <span
                  key={e.symbol}
                  className="inline-flex items-center gap-1.5 rounded-md border border-border bg-background px-2 py-1 text-sm"
                >
                  <span className="font-semibold">{e.symbol}</span>
                  {has ? (
                    <span className={up ? "text-emerald-500" : "text-red-500"}>
                      {up ? "+" : ""}
                      {move}%
                    </span>
                  ) : (
                    <span className="text-muted-foreground">—</span>
                  )}
                  <span className="text-xs text-muted-foreground">
                    {e.report_date.slice(5, 10)}
                    {e.session ? ` ${e.session}` : ""}
                  </span>
                </span>
              )
            })}
          </div>
        </Panel>
      )}

      {earnings.upcoming.length > 0 && (
        <Panel title="Reporting soon" sub="grouped by when to run the desk — biggest names first">
          <div className="space-y-3">
            {groupByRunDay(earnings.upcoming).map((g) => {
              const shown = g.rows.slice(0, 8)
              const more = g.rows.length - shown.length
              return (
                <div key={g.runDay}>
                  <div className="mb-1 text-xs font-semibold text-emerald-500">
                    {g.runDay === "—" ? "Run time n/a" : `Run ${runDayLabel(g.runDay)} · 9:30 ET`}
                  </div>
                  <ul className="divide-y divide-border">
                    {shown.map((e) => (
                      <li
                        key={e.symbol + e.report_date}
                        className="flex items-center gap-2 py-1.5 text-sm"
                      >
                        <span className="w-14 font-semibold">{e.symbol}</span>
                        <span className="w-14 text-xs text-muted-foreground">
                          {fmtCap(e.market_cap)}
                        </span>
                        <span className="ml-auto text-xs text-muted-foreground">
                          {e.report_date.slice(5, 10)} {e.session}
                        </span>
                      </li>
                    ))}
                  </ul>
                  {more > 0 && (
                    <div className="mt-1 text-xs text-muted-foreground">+{more} more</div>
                  )}
                </div>
              )
            })}
          </div>
        </Panel>
      )}
    </div>
  )
}
