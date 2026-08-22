import { useMemo, useState } from "react"
import { useQuery } from "@tanstack/react-query"
import { api, type MetricPeriod } from "@/lib/api"
import { Empty, Widget } from "@/components/terminal"

/** Reported financials for ONE company, grouped the way the server groups them.
 *
 * The tabs are NOT a fixed list. /api/fundamentals only returns metrics the
 * upstream actually reports, each already carrying its `group`, so the tab bar
 * is derived from the response — a company that reports no cash-flow lines
 * simply has no Cash flow tab. That is the same rule the endpoint documents for
 * metrics ("a menu entry that would draw an empty line is not offered at all"),
 * applied one level up. It is also why there is no Balance Sheet tab: we have
 * no balance-sheet data, and an empty tab is a promise the panel cannot keep.
 *
 * The endpoint has existed and been unreferenced since the chart's metric
 * overlay was removed; this is its first caller.
 */
const compact = (n: number): string => {
  const a = Math.abs(n)
  if (a >= 1e12) return `${(n / 1e12).toFixed(2)}T`
  if (a >= 1e9) return `${(n / 1e9).toFixed(2)}B`
  if (a >= 1e6) return `${(n / 1e6).toFixed(2)}M`
  if (a >= 1e3) return `${(n / 1e3).toFixed(1)}K`
  return n.toFixed(2)
}

const PERIODS: { id: MetricPeriod; label: string }[] = [
  { id: "annual", label: "Annual" },
  { id: "quarterly", label: "Quarterly" },
]
const COLS = 5

export function SymbolFundamentals({ symbol }: { symbol: string }) {
  const [period, setPeriod] = useState<MetricPeriod>("annual")
  const [group, setGroup] = useState<string | null>(null)

  const { data, isPending } = useQuery({
    queryKey: ["fundamentals", symbol, period],
    queryFn: () => api.fundamentals(symbol, period),
    enabled: !!symbol,
    staleTime: 5 * 60_000,
  })

  const groups = useMemo(
    () => [...new Set((data?.metrics ?? []).map(m => m.group))],
    [data],
  )
  const active = group && groups.includes(group) ? group : groups[0] ?? null
  const rows = (data?.metrics ?? []).filter(m => m.group === active)

  // Metrics can report on different dates, so the columns are the union of
  // every period seen — newest first, and the same columns for every row.
  const cols = useMemo(() => {
    const seen = new Set<string>()
    for (const m of rows) for (const p of data?.series?.[m.id] ?? []) seen.add(p.t)
    return [...seen].sort().reverse().slice(0, COLS).reverse()
  }, [rows, data])

  return (
    <Widget
      span={12}
      symbol={symbol}
      title="Fundamentals"
      subtitle={data?.metrics?.length ? `${groups.length} reported` : undefined}
      toolbar={
        <>
          {groups.map(g => (
            <button
              key={g}
              onClick={() => setGroup(g)}
              aria-pressed={g === active}
              className={`rounded-sm px-2 py-[3px] text-[12px] leading-none transition-colors ${
                g === active ? "bg-muted font-medium text-foreground"
                             : "text-muted-foreground hover:bg-muted/60 hover:text-foreground"}`}
            >
              {g}
            </button>
          ))}
          <span className="flex-1" />
          {PERIODS.map(p => (
            <button
              key={p.id}
              onClick={() => setPeriod(p.id)}
              aria-pressed={p.id === period}
              className={`rounded-sm px-2 py-[3px] text-[12px] leading-none transition-colors ${
                p.id === period ? "bg-muted font-medium text-foreground"
                                : "text-muted-foreground hover:bg-muted/60 hover:text-foreground"}`}
            >
              {p.label}
            </button>
          ))}
        </>
      }
      scroll={260}
    >
      {isPending ? <Empty>loading…</Empty>
       : !rows.length ? <Empty>no financials reported for {symbol}</Empty> : (
        <table className="w-full border-collapse text-[14px]">
          <thead>
            <tr className="text-[10px] font-medium uppercase tracking-[1px] text-muted-foreground">
              <th className="px-3 py-[10px] text-left font-medium">Metric</th>
              {cols.map(t => (
                <th key={t} className="px-3 py-[10px] text-right font-medium">{t}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map(m => {
              const by = new Map((data?.series?.[m.id] ?? []).map(p => [p.t, p.v]))
              return (
                <tr key={m.id} className="row-rule">
                  <td className="px-3 py-[6px] text-muted-foreground">{m.label}</td>
                  {cols.map(t => {
                    const v = by.get(t)
                    return (
                      <td key={t} className="tnum px-3 py-[6px] text-right">
                        {/* A missing period is a dash, never a zero — the two
                            mean opposite things on an income statement. */}
                        {v == null ? "—" : m.unit === "eps" ? v.toFixed(2) : compact(v)}
                      </td>
                    )
                  })}
                </tr>
              )
            })}
          </tbody>
        </table>
      )}
    </Widget>
  )
}
