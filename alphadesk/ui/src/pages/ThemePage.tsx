import { useMemo, useState } from "react"
import { Link, useParams, useSearchParams } from "react-router-dom"
import type { Quote } from "@/lib/api"
import { useQuotes, useThemes } from "@/lib/queries"
import { Empty, Flash, Widget } from "@/components/terminal"
import { SymbolChart } from "@/components/SymbolChart"
import { SymbolNews } from "@/components/SymbolNews"

/** One curated basket: its members, and whichever member you are looking at.
 *
 * The Themes rail was here once and was deleted, because the links carried a
 * ?q= nothing read — buttons that looked like filters and filtered nothing.
 * The note left behind said to rebuild it on top of a real surface rather than
 * before one. This is that surface: every row is a live quote, and the chart
 * and news below are scoped to the row you pick.
 *
 * The whole basket is priced in ONE request. Each row fetching its own looked
 * fine and was not: nine concurrent per-symbol calls land on nine threadpool
 * workers, the upstream throttled, and two or three came back 404 at random —
 * so those rows rendered as dashes, which reads as "no price exists" rather
 * than "we asked too fast". /api/quotes walks the list server-side and fills
 * the same per-symbol cache on the way, so opening one of these names on
 * Analysis afterwards is already answered.
 */
const compact = (n: number | null | undefined): string => {
  if (n == null) return "—"
  const a = Math.abs(n)
  if (a >= 1e12) return `${(n / 1e12).toFixed(2)}T`
  if (a >= 1e9) return `${(n / 1e9).toFixed(2)}B`
  if (a >= 1e6) return `${(n / 1e6).toFixed(1)}M`
  if (a >= 1e3) return `${(n / 1e3).toFixed(1)}K`
  return n.toFixed(2)
}
const money = (n: number | null | undefined, d = 2) =>
  n == null ? "—" : n.toLocaleString(undefined, { minimumFractionDigits: d, maximumFractionDigits: d })

/** The four measures their heatmap offers. All are already on /api/quote. */
const HEAT = [
  { id: "market_cap", label: "Market cap", fmt: compact },
  { id: "volume", label: "Volume", fmt: compact },
  { id: "avg_volume", label: "Avg vol", fmt: compact },
  { id: "pe_trailing", label: "P/E", fmt: (n: number | null | undefined) => n == null ? "—" : n.toFixed(1) },
] as const

type Heat = (typeof HEAT)[number]["id"]

function Row({ symbol, data, loading, active, onPick }: {
  symbol: string
  data: Quote | null | undefined
  loading: boolean
  active: boolean
  onPick: () => void
}) {
  const isPending = loading
  const chg = data?.change_pct ?? null
  const up = (chg ?? 0) >= 0
  return (
    <tr
      onClick={onPick}
      className={`row-rule cursor-pointer ${active ? "bg-muted" : "hover:bg-muted/50"}`}
    >
      <td className="px-3 py-[6px]">
        <span className="num font-semibold">{symbol}</span>
      </td>
      <td className="max-w-[220px] truncate px-3 py-[6px] text-muted-foreground">
        {isPending ? "…" : data?.name ?? ""}
      </td>
      <td className="tnum px-3 py-[6px] text-right">
        <Flash value={data?.price}>{money(data?.price)}</Flash>
      </td>
      <td className={`tnum px-3 py-[6px] text-right ${
        chg == null ? "text-muted-foreground" : up ? "text-gain" : "text-loss"}`}>
        {chg == null ? "—" : `${up ? "+" : ""}${chg.toFixed(2)}%`}
      </td>
      <td className="tnum px-3 py-[6px] text-right text-muted-foreground">{compact(data?.volume)}</td>
      <td className="tnum px-3 py-[6px] text-right text-muted-foreground">{money(data?.week52_low)}</td>
      <td className="tnum px-3 py-[6px] text-right text-muted-foreground">{money(data?.week52_high)}</td>
      <td className="tnum px-3 py-[6px] text-right">{compact(data?.market_cap)}</td>
    </tr>
  )
}

/** One heatmap cell. Area is not encoded — these are equal-sized tiles tinted
 * by direction and labelled with the chosen measure. A treemap sized by market
 * cap would put NVDA at forty times AAPL's area and make the other six
 * unreadable, which is a worse answer than a plain grid. */
function HeatCell({ symbol, data, metric, onPick }: {
  symbol: string
  data: Quote | null | undefined
  metric: Heat
  onPick: () => void
}) {
  const chg = data?.change_pct ?? null
  const up = (chg ?? 0) >= 0
  const spec = HEAT.find(h => h.id === metric)!
  const raw = data ? (data[metric] as number | null | undefined) : null
  return (
    <button
      onClick={onPick}
      className="flex min-w-0 flex-col items-start gap-0.5 rounded border border-grid-line px-2 py-2 text-left transition-colors hover:border-accent"
      style={{
        // Tint by magnitude of the move, clamped: past ~5% every cell would
        // saturate and the grid would stop distinguishing anything.
        backgroundColor: chg == null ? undefined
          : `color-mix(in srgb, var(--${up ? "gain" : "loss"}) ${
              Math.min(Math.abs(chg) / 5, 1) * 22}%, transparent)`,
      }}
    >
      <span className="num text-[14px] font-semibold">{symbol}</span>
      <span className={`tnum text-[13px] ${chg == null ? "text-muted-foreground" : up ? "text-gain" : "text-loss"}`}>
        {chg == null ? "—" : `${up ? "+" : ""}${chg.toFixed(2)}%`}
      </span>
      <span className="tnum text-[12px] text-muted-foreground">{spec.fmt(raw)}</span>
    </button>
  )
}

export default function ThemePage() {
  const { id } = useParams()
  const [params, setParams] = useSearchParams()
  const { data, isPending } = useThemes()
  const [metric, setMetric] = useState<Heat>("market_cap")

  const theme = useMemo(
    () => (data?.themes ?? []).find(t => t.id === id) ?? null,
    [data, id],
  )
  const symbols = useMemo(() => theme?.symbols ?? [], [theme])
  const quotes = useQuotes(symbols)
  const priced = quotes.data?.quotes
  const picked = params.get("symbol")?.toUpperCase() || symbols[0] || ""
  const pick = (sym: string) => {
    const next = new URLSearchParams(params)
    next.set("symbol", sym)
    setParams(next, { replace: true })
  }

  if (isPending) return <div className="collage"><Widget span={12} title="Theme"><Empty>loading…</Empty></Widget></div>
  if (!theme) {
    return (
      <div className="collage">
        <Widget span={12} title="Theme">
          <Empty>no theme called “{id}” — check THEMES in the server config</Empty>
        </Widget>
      </div>
    )
  }

  return (
    <div className="collage">
      <Widget
        span={12}
        title={theme.label}
        subtitle={`${symbols.length} names · pick a row to scope the chart`}
        scroll={320}
        bodyClassName="overflow-x-auto"
      >
        <table className="w-full border-collapse text-[14px]">
          <thead>
            <tr className="text-[10px] font-medium uppercase tracking-[1px] text-muted-foreground">
              <th className="px-3 py-[10px] text-left font-medium">Symbol</th>
              <th className="px-3 py-[10px] text-left font-medium">Name</th>
              <th className="px-3 py-[10px] text-right font-medium">Price</th>
              <th className="px-3 py-[10px] text-right font-medium">Chg %</th>
              <th className="px-3 py-[10px] text-right font-medium">Volume</th>
              <th className="px-3 py-[10px] text-right font-medium">52w Low</th>
              <th className="px-3 py-[10px] text-right font-medium">52w High</th>
              <th className="px-3 py-[10px] text-right font-medium">Mkt Cap</th>
            </tr>
          </thead>
          <tbody>
            {symbols.map(s => (
              <Row key={s} symbol={s} data={priced?.[s]} loading={quotes.isPending}
                   active={s === picked} onPick={() => pick(s)} />
            ))}
          </tbody>
        </table>
      </Widget>

      <Widget
        span={12}
        title="Heatmap"
        subtitle="tinted by today's move"
        toolbar={
          <>
            {HEAT.map(h => (
              <button
                key={h.id}
                onClick={() => setMetric(h.id)}
                aria-pressed={h.id === metric}
                className={`rounded px-2 py-[3px] text-[12px] leading-none transition-colors ${
                  h.id === metric ? "bg-muted font-medium text-foreground"
                                  : "text-muted-foreground hover:bg-muted/60 hover:text-foreground"}`}
              >
                {h.label}
              </button>
            ))}
          </>
        }
      >
        <div className="grid gap-1.5 p-2"
             style={{ gridTemplateColumns: "repeat(auto-fill, minmax(96px, 1fr))" }}>
          {symbols.map(s => (
            <HeatCell key={s} symbol={s} data={priced?.[s]} metric={metric}
                      onPick={() => pick(s)} />
          ))}
        </div>
      </Widget>

      {picked && <SymbolChart symbol={picked} span={8} />}
      {picked && <SymbolNews symbol={picked} span={4} scroll={420} />}
      <div className="col-span-12 px-1 text-[12px] text-muted-foreground">
        <Link to={`/analysis?symbol=${encodeURIComponent(picked)}`} className="hover:underline">
          Open {picked} in Analysis →
        </Link>
      </div>
    </div>
  )
}
