import { useMemo } from "react"
import { useQuery } from "@tanstack/react-query"
import { api, type EarningsRow } from "@/lib/api"
import { useEarningsWeek } from "@/lib/queries"
import { Empty, Widget } from "@/components/terminal"

/** The company-scoped column beside the earnings calendar.
 *
 * Their earnings view runs four panels here: revenue vs earnings, EPS estimate
 * against actual, analyst estimates, and news. Three of those we can answer.
 * Analyst estimate tables we cannot — /api/quote carries a target and a rating
 * but not the per-quarter consensus grid — so that panel is absent rather than
 * present and empty.
 *
 * EPS needs no new data at all: every calendar row already carries
 * eps_estimate, eps_actual, surprise_pct and the options-implied move.
 */
const money = (n: number | null | undefined): string => {
  if (n == null) return "—"
  const a = Math.abs(n)
  if (a >= 1e12) return `${(n / 1e12).toFixed(2)}T`
  if (a >= 1e9) return `${(n / 1e9).toFixed(2)}B`
  if (a >= 1e6) return `${(n / 1e6).toFixed(1)}M`
  return n.toFixed(2)
}
const eps = (n: number | null | undefined) =>
  n == null ? "—" : `${n >= 0 ? "+" : ""}${n.toFixed(2)}`

function Line({ label, value, tone }: {
  label: string; value: React.ReactNode; tone?: "gain" | "loss"
}) {
  return (
    <div className="row-rule flex items-baseline justify-between gap-3 px-3 py-[6px]">
      <span className="shrink-0 text-[14px] text-muted-foreground">{label}</span>
      <span className={`tnum text-[14px] ${tone ? `text-${tone}` : ""}`}>{value}</span>
    </div>
  )
}

export function EarningsDetail({ symbol }: { symbol: string }) {
  const week = useEarningsWeek()
  const row: EarningsRow | undefined = useMemo(() => {
    for (const d of week.data?.days ?? []) {
      const hit = (d.rows ?? []).find(r => r.symbol === symbol)
      if (hit) return hit
    }
    return undefined
  }, [week.data, symbol])

  // Revenue and net income, the two series their "Revenue vs. Earnings" panel
  // plots. Annual, because a quarterly pair on a 280px column is unreadable.
  const fin = useQuery({
    queryKey: ["fundamentals", symbol, "annual"],
    queryFn: () => api.fundamentals(symbol, "annual"),
    enabled: !!symbol,
    staleTime: 5 * 60_000,
  })
  const rev = fin.data?.series?.revenue ?? []
  const net = fin.data?.series?.net_income ?? []
  const periods = rev.slice(-4)

  const surprise = row?.surprise_pct ?? null

  return (
    <>
      <Widget span={4} symbol={symbol} title="Earnings" subtitle="estimate vs actual" scroll={210}>
        {week.isPending ? <Empty>loading…</Empty>
         : !row ? <Empty>{symbol} does not report this week</Empty> : (
          <div>
            <Line label="Report date" value={row.report_date} />
            <Line label="Session" value={row.session ?? "—"} />
            <Line label="Estimate" value={eps(row.eps_estimate)} />
            <Line label="Actual" value={eps(row.eps_actual)} />
            <Line
              label="Surprise"
              value={surprise == null ? "—" : `${surprise >= 0 ? "+" : ""}${surprise.toFixed(2)}%`}
              tone={surprise == null ? undefined : surprise >= 0 ? "gain" : "loss"}
            />
            {/* The market's own expectation, which is the one number here that
                is a forecast rather than a report. */}
            <Line
              label="Implied move"
              value={row.implied_move_pct == null ? "—" : `±${row.implied_move_pct.toFixed(1)}%`}
            />
          </div>
        )}
      </Widget>

      <Widget span={4} symbol={symbol} title="Revenue vs Earnings" subtitle="annual" scroll={210}>
        {fin.isPending ? <Empty>loading…</Empty>
         : !periods.length ? <Empty>no reported financials for {symbol}</Empty> : (
          <div>
            <div className="flex items-center px-3 py-[10px] text-[10px] font-medium uppercase tracking-[1px] text-muted-foreground">
              <span className="min-w-0 flex-1">Period</span>
              <span className="w-[84px] text-right">Revenue</span>
              <span className="w-[84px] text-right">Net income</span>
            </div>
            {periods.map(p => {
              const ni = net.find(n => n.t === p.t)
              return (
                <div key={p.t} className="row-rule flex items-center px-3 py-[7px] text-[14px]">
                  <span className="min-w-0 flex-1 truncate text-muted-foreground">{p.t}</span>
                  <span className="tnum w-[84px] text-right">{money(p.v)}</span>
                  {/* A loss is a real outcome, so it is tinted rather than
                      rendered as an unsigned figure that reads like a profit. */}
                  <span className={`tnum w-[84px] text-right ${
                    ni == null ? "text-muted-foreground" : ni.v < 0 ? "text-loss" : ""}`}>
                    {ni == null ? "—" : money(ni.v)}
                  </span>
                </div>
              )
            })}
          </div>
        )}
      </Widget>
    </>
  )
}
