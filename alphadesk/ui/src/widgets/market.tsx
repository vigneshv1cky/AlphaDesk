import { useState } from "react"
import { Link, useSearchParams } from "react-router-dom"
import type { MoverRow } from "@/lib/api"
import { useMovers, useQuote } from "@/lib/queries"
import { Empty, Sparkline, Widget } from "@/components/terminal"
import { registerWidget } from "@/widgets/registry"

/** Equity Overview and Movers — the two tiles that make a markets board feel
 * like a quote terminal rather than a news reader. */

function compact(n: number | null | undefined): string {
  if (n == null) return "—"
  const abs = Math.abs(n)
  if (abs >= 1e12) return `${(n / 1e12).toFixed(2)}T`
  if (abs >= 1e9) return `${(n / 1e9).toFixed(2)}B`
  if (abs >= 1e6) return `${(n / 1e6).toFixed(2)}M`
  if (abs >= 1e3) return n.toLocaleString(undefined, { maximumFractionDigits: 0 })
  return n.toFixed(2)
}

const num = (n: number | null | undefined, d = 2) =>
  n == null ? "—" : n.toLocaleString(undefined, { minimumFractionDigits: d, maximumFractionDigits: d })

function Row({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="flex items-baseline justify-between gap-3 border-b border-grid-line px-3 py-[5px] last:border-b-0">
      <span className="shrink-0 text-[11px] text-muted-foreground">{label}</span>
      <span className="num truncate text-[11px]">{value}</span>
    </div>
  )
}

/** The quote block. Scoped by ?symbol= like every other widget, so changing
 * the chip in the view header re-points it along with the rest of the board. */
function EquityOverview() {
  const [params] = useSearchParams()
  const symbol = (params.get("symbol") || "").toUpperCase()
  const { data: q, isPending, error } = useQuote(symbol)

  if (!symbol) {
    return (
      <Widget span={4} title="Equity Overview">
        <Empty>Pick a symbol to scope this board — use the chip in the header, or open a chart.</Empty>
      </Widget>
    )
  }
  if (error) {
    return <Widget span={4} symbol={symbol} title="Equity Overview"><Empty>no quote for {symbol}</Empty></Widget>
  }
  if (isPending || !q) {
    return <Widget span={4} symbol={symbol} title="Equity Overview"><Empty>loading…</Empty></Widget>
  }

  const up = (q.change ?? 0) >= 0
  return (
    <Widget span={4} symbol={q.symbol} title="Equity Overview" scroll={420}>
      <div className="px-3 pb-2">
        <div className="text-[10px] text-muted-foreground">
          {q.exchange} · {q.currency}
        </div>
        <div className="text-[12px] font-medium">{q.name}</div>
        <div className="num mt-1 text-[26px] font-semibold leading-none">{num(q.price)}</div>
        <div className={`num mt-1 text-[11px] ${up ? "text-gain" : "text-loss"}`}>
          {up ? "+" : ""}{num(q.change)} ({up ? "+" : ""}{num(q.change_pct)}%)
        </div>
      </div>
      <Row label="Previous Close" value={num(q.previous_close)} />
      <Row label="Open" value={num(q.open)} />
      <Row label="Bid" value={q.bid ? `${num(q.bid)} x ${q.bid_size ?? 0}` : "—"} />
      <Row label="Ask" value={q.ask ? `${num(q.ask)} x ${q.ask_size ?? 0}` : "—"} />
      <Row label="Day's Range" value={q.day_low ? `${num(q.day_low)} - ${num(q.day_high)}` : "—"} />
      <Row label="52 Week Range" value={q.week52_low ? `${num(q.week52_low)} - ${num(q.week52_high)}` : "—"} />
      <Row label="Volume" value={q.volume?.toLocaleString() ?? "—"} />
      <Row label="Avg. Volume" value={q.avg_volume?.toLocaleString() ?? "—"} />
      <Row label="Market Cap" value={compact(q.market_cap)} />
      <Row label="Enterprise Value" value={compact(q.enterprise_value)} />
      <Row label="PE Ratio (Forward)" value={num(q.pe_forward)} />
      <Row label="PE Ratio (TTM)" value={num(q.pe_trailing)} />
      <Row label="PEG Ratio" value={num(q.peg)} />
      <Row label="Price/Sales (TTM)" value={num(q.price_to_sales)} />
      <Row label="Price/Book" value={num(q.price_to_book)} />
      <Row label="Beta (5Y Monthly)" value={num(q.beta)} />
      <Row label="EPS (TTM)" value={num(q.eps_ttm)} />
      <Row label="1y Target Est" value={num(q.target_mean)} />
      <Row
        label="Analyst Target Range"
        value={q.target_low ? `${num(q.target_low)} - ${num(q.target_high)}` : "—"}
      />
      <Row
        label="Analyst Rating"
        value={q.analyst_rating
          ? <span className="capitalize">{q.analyst_rating}{q.analyst_count ? ` (${q.analyst_count})` : ""}</span>
          : "—"}
      />
    </Widget>
  )
}

const TABS = [
  { id: "most_active", label: "Most Active" },
  { id: "gainers", label: "Gainers" },
  { id: "losers", label: "Losers" },
] as const

function MoversTable({ rows }: { rows: MoverRow[] }) {
  if (!rows.length) {
    return <Empty>nothing clears the price and turnover floors right now</Empty>
  }
  return (
    <div>
      <div className="flex items-center border-b border-grid-line px-3 py-1 text-[10px] uppercase tracking-[0.06em] text-muted-foreground">
        <span className="flex-1">Symbol</span>
        <span className="w-[72px]" />
        <span className="w-20 text-right">Price</span>
        <span className="w-20 text-right">Chg %</span>
        <span className="w-24 text-right">Volume</span>
      </div>
      {rows.map(r => {
        const up = (r.change_pct ?? 0) >= 0
        return (
          <Link
            key={r.symbol}
            to={`/chart?symbol=${encodeURIComponent(r.symbol)}`}
            className="flex items-center border-b border-grid-line px-3 py-[6px] text-[11px] last:border-b-0 hover:bg-muted/60"
          >
            <span className="num flex-1 font-semibold text-accent">{r.symbol}</span>
            {/* Tinted by direction, same as the change cell — the line and the
                number should never disagree about which way the day went. */}
            <span className={`w-[72px] ${up ? "text-gain" : "text-loss"}`}>
              <Sparkline points={r.spark} />
            </span>
            <span className="num w-20 text-right">{r.price?.toFixed(2) ?? "—"}</span>
            <span className={`num w-20 text-right ${up ? "text-gain" : "text-loss"}`}>
              {r.change_pct == null ? "—" : `${up ? "+" : ""}${r.change_pct.toFixed(2)}%`}
            </span>
            <span className="num w-24 text-right text-muted-foreground">{compact(r.volume)}</span>
          </Link>
        )
      })}
    </div>
  )
}

function StockMovers() {
  const [tab, setTab] = useState<(typeof TABS)[number]["id"]>("most_active")
  const { data, isPending } = useMovers()
  return (
    <Widget
      span={8}
      title="Stock Movers"
      subtitle="≥ $5 and ≥ $1M turnover — a percentage screen without a liquidity floor is all pumps"
      scroll={420}
    >
      <div className="flex gap-1 px-3 pb-2">
        {TABS.map(t => (
          <button
            key={t.id}
            onClick={() => setTab(t.id)}
            className={`rounded-md px-2.5 py-1 text-[11px] transition-colors ${
              tab === t.id
                ? "bg-muted font-medium text-foreground"
                : "text-muted-foreground hover:bg-muted/60 hover:text-foreground"
            }`}
          >
            {t.label}
          </button>
        ))}
      </div>
      {isPending ? <Empty>loading…</Empty> : <MoversTable rows={data?.[tab] ?? []} />}
    </Widget>
  )
}

registerWidget({ id: "equity-overview", order: 15, component: EquityOverview })
registerWidget({ id: "stock-movers", order: 16, component: StockMovers })
