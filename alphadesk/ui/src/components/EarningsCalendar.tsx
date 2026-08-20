import { useMemo, useState } from "react"
import { Link } from "react-router-dom"
import type { EarningsDay, EarningsWeek } from "@/lib/api"
import { useEarningsWeek } from "@/lib/queries"
import { Empty } from "@/components/terminal"

/** The earnings calendar as a WEEK, not a two-bucket split.
 *
 * A reporting season is read one week at a time — "who is on Thursday" is the
 * question, and the old upcoming/reported split could not answer it because
 * both halves were sorted by their own clocks. So: a seven-cell strip with the
 * call count per day, then one table per day.
 *
 * Weekend cells are rendered even though US equities never report then. Seven
 * cells is what makes the strip readable as a week at a glance; dropping the
 * empty ones would shift every other day sideways depending on the month.
 *
 * Rows come back biggest-cap-first from the server — on a 50-name day that is
 * the difference between scanning and hunting. */

function money(n: number | null | undefined): string {
  if (n == null || n === 0) return "—"
  const abs = Math.abs(n)
  if (abs >= 1e12) return `${(n / 1e12).toFixed(2)}T`
  if (abs >= 1e9) return `${(n / 1e9).toFixed(2)}B`
  if (abs >= 1e6) return `${(n / 1e6).toFixed(2)}M`
  if (abs >= 1e3) return `${(n / 1e3).toFixed(1)}K`
  return n.toFixed(0)
}

function eps(n: number | null | undefined): string {
  return n == null ? "—" : n.toFixed(2)
}

/** "Sunday, August 16" — the heading over each day's table. */
function longDay(iso: string): string {
  return new Date(`${iso}T12:00:00`).toLocaleDateString("en-US", {
    weekday: "long", month: "long", day: "numeric",
  })
}

function rangeLabel(start: string, end: string): string {
  const a = new Date(`${start}T12:00:00`)
  const b = new Date(`${end}T12:00:00`)
  const m = (d: Date) => d.toLocaleDateString("en-US", { month: "short", day: "numeric" })
  return `${m(a)} — ${m(b)}, ${b.getFullYear()}`
}

function shiftWeek(start: string, weeks: number): string {
  const d = new Date(`${start}T12:00:00`)
  d.setDate(d.getDate() + weeks * 7)
  return d.toISOString().slice(0, 10)
}

/** One day cell in the strip. Count is the headline: it is what tells you
 * which day of the week actually matters. */
function DayCell({
  day, today, active, onSelect,
}: {
  day: EarningsDay
  today: string
  active: boolean
  onSelect: () => void
}) {
  const isToday = day.date === today
  return (
    <button
      type="button"
      onClick={onSelect}
      className={`flex min-w-0 flex-1 flex-col items-start gap-0.5 border-r border-grid-line px-2.5 py-2 text-left last:border-r-0 transition-colors ${
        active ? "bg-muted" : "hover:bg-muted/50"
      }`}
    >
      <span className={`text-[12px] uppercase tracking-[0.08em] ${
        isToday ? "font-semibold text-accent" : "text-muted-foreground"
      }`}>
        {day.weekday}
      </span>
      <span className={`num text-[16px] ${isToday ? "font-semibold text-accent" : "text-foreground"}`}>
        {day.date.slice(8)}
      </span>
      <span className="truncate text-[12px] text-muted-foreground">
        {day.count === 0 ? "No calls" : `${day.count} ${day.count === 1 ? "call" : "calls"}`}
      </span>
    </button>
  )
}

function DayTable({ day }: { day: EarningsDay }) {
  if (!day.rows.length) return null
  return (
    <section id={`day-${day.date}`} className="scroll-mt-2">
      <h3 className="border-b border-grid-line bg-panel-header px-3 py-1.5 text-[14px] font-semibold">
        {longDay(day.date)}
        <span className="ml-2 font-normal text-muted-foreground">
          {day.count} {day.count === 1 ? "call" : "calls"}
        </span>
      </h3>
      <table className="w-full min-w-[640px] border-collapse">
          <thead>
            <tr className="text-[10px] font-medium uppercase tracking-[1px] text-muted-foreground">
              <th className="sticky top-0 z-10 bg-panel px-[12px] py-[14px] text-left font-medium">Symbol</th>
              <th className="sticky top-0 z-10 bg-panel px-[12px] py-[14px] text-left font-medium" />
              <th className="sticky top-0 z-10 bg-panel px-[12px] py-[14px] text-right font-medium">Estimated EPS</th>
              <th className="sticky top-0 z-10 bg-panel px-[12px] py-[14px] text-right font-medium">Actual EPS</th>
              <th className="sticky top-0 z-10 bg-panel px-[12px] py-[14px] text-right font-medium">Surprise</th>
              <th className="sticky top-0 z-10 bg-panel px-[12px] py-[14px] text-right font-medium">Market cap</th>
            </tr>
          </thead>
          <tbody>
            {day.rows.map(r => {
              const surprise = r.surprise_pct
              return (
                <tr key={`${r.symbol}-${r.report_date}`} className="hover:bg-muted/50">
                  <td className="px-[12px] py-[6px] text-[14px]">
                    <Link
                      to={`/analysis?symbol=${encodeURIComponent(r.symbol)}`}
                      className="num font-semibold text-accent hover:underline"
                    >
                      {r.symbol}
                    </Link>
                  </td>
                  <td className="max-w-[280px] truncate px-[12px] py-[6px] text-[14px] text-muted-foreground">
                    {r.company_name ?? ""}
                  </td>
                  <td className="num px-[12px] py-[6px] text-right text-[14px]">{eps(r.eps_estimate)}</td>
                  <td className="num px-[12px] py-[6px] text-right text-[14px]">{eps(r.eps_actual)}</td>
                  <td className={`num px-[12px] py-[6px] text-right text-[14px] ${
                    surprise == null ? "text-muted-foreground"
                      : surprise >= 0 ? "text-gain" : "text-loss"
                  }`}>
                    {surprise == null ? "—" : `${surprise >= 0 ? "+" : ""}${surprise.toFixed(2)}%`}
                  </td>
                  <td className="num px-[12px] py-[6px] text-right text-[14px] text-muted-foreground">
                    {money(r.market_cap)}
                  </td>
                </tr>
              )
            })}
          </tbody>
      </table>
    </section>
  )
}

export function EarningsCalendar() {
  const [start, setStart] = useState<string | undefined>(undefined)
  const { data, isPending, isError } = useEarningsWeek(start)
  const [selected, setSelected] = useState<string | null>(null)

  const week: EarningsWeek | undefined = data
  const total = useMemo(
    () => (week?.days ?? []).reduce((n, d) => n + d.count, 0),
    [week],
  )

  if (isPending) return <Empty>loading…</Empty>
  if (isError || !week) return <Empty>the calendar could not be loaded</Empty>

  // Selecting a day filters the tables to it; selecting it again clears.
  const shown = selected ? week.days.filter(d => d.date === selected) : week.days

  return (
    <div>
      <div className="flex flex-wrap items-center gap-2 border-b border-grid-line px-3 py-1.5">
        <button
          type="button"
          onClick={() => { setStart(undefined); setSelected(null) }}
          className="border border-border px-2 py-[3px] text-[14px] text-muted-foreground hover:bg-muted hover:text-foreground"
        >
          Today
        </button>
        <div className="flex items-center">
          <button
            type="button"
            aria-label="Previous week"
            onClick={() => { setStart(shiftWeek(week.start, -1)); setSelected(null) }}
            className="px-2 py-[3px] text-[14px] text-muted-foreground hover:bg-muted hover:text-foreground"
          >
            ‹
          </button>
          <button
            type="button"
            aria-label="Next week"
            onClick={() => { setStart(shiftWeek(week.start, 1)); setSelected(null) }}
            className="px-2 py-[3px] text-[14px] text-muted-foreground hover:bg-muted hover:text-foreground"
          >
            ›
          </button>
        </div>
        <span className="num text-[15px] font-semibold">{rangeLabel(week.start, week.end)}</span>
        <span className="text-[14px] text-muted-foreground">
          {total} {total === 1 ? "call" : "calls"} this week
        </span>
        {selected && (
          <button
            type="button"
            onClick={() => setSelected(null)}
            className="ml-auto text-[14px] text-accent hover:underline"
          >
            show the whole week
          </button>
        )}
      </div>

      <div className="flex border-b border-grid-line">
        {week.days.map(d => (
          <DayCell
            key={d.date}
            day={d}
            today={week.today}
            active={selected === d.date}
            onSelect={() => setSelected(selected === d.date ? null : d.date)}
          />
        ))}
      </div>

      {total === 0 ? (
        <Empty>nothing reports this week</Empty>
      ) : (
        shown.map(d => <DayTable key={d.date} day={d} />)
      )}
    </div>
  )
}
