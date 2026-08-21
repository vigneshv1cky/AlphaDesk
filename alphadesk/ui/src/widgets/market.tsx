import { useState } from "react"
import { Link, useSearchParams } from "react-router-dom"
import type { MoverRow } from "@/lib/api"
import { useMovers, useQuote } from "@/lib/queries"
import { useLiveTrade } from "@/lib/live"
import { Empty, Flash, Sparkline, Widget } from "@/components/terminal"
import { registerWidget } from "@/widgets/registry"
import { TILE_BODY_HEIGHT } from "@/widgets/tile"

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
    <div className="row-rule flex items-baseline justify-between gap-3 px-3 py-[5px]">
      <span className="shrink-0 text-[14px] text-muted-foreground">{label}</span>
      <span className="num truncate text-[14px]">{value}</span>
    </div>
  )
}

/** The quote block. Scoped by ?symbol= like every other widget, so changing
 * the chip in the view header re-points it along with the rest of the board. */
function EquityOverview() {
  const [params] = useSearchParams()
  const symbol = (params.get("symbol") || "").toUpperCase()
  const { data: q, isPending, error } = useQuote(symbol)
  const { tick } = useLiveTrade(symbol)

  if (!symbol) {
    return (
      <Widget span={4} title="Equity Overview" scroll={TILE_BODY_HEIGHT}>
        <Empty>Pick a symbol to scope this board — use the chip in the header, or open a chart.</Empty>
      </Widget>
    )
  }
  if (error) {
    return <Widget span={4} symbol={symbol} title="Equity Overview" scroll={TILE_BODY_HEIGHT}><Empty>no quote for {symbol}</Empty></Widget>
  }
  if (isPending || !q) {
    return <Widget span={4} symbol={symbol} title="Equity Overview"><Empty>loading…</Empty></Widget>
  }

  /** The headline price rides the live feed; everything below it stays on the
   * quote. Change is RECOMPUTED against the previous close rather than left as
   * the quote reported it — showing a moved price beside a change that has not
   * moved would be the two disagreeing on screen. Only the fields a trade
   * actually determines are touched; the ranges, multiples and targets are the
   * quote's to state. */
  const fresh = tick && !tick.stale && tick.symbol === q.symbol ? tick.price : null
  const price = fresh ?? q.price
  const prev = q.previous_close
  const change = fresh != null && prev ? fresh - prev : q.change
  const changePct = fresh != null && prev ? ((fresh - prev) / prev) * 100 : q.change_pct

  const up = (change ?? 0) >= 0
  return (
    <Widget span={4} symbol={q.symbol} title="Equity Overview" scroll={TILE_BODY_HEIGHT}>
      <div className="px-3 pb-2">
        <div className="text-[12px] text-muted-foreground">
          {[q.exchange_name || q.exchange, q.quote_source].filter(Boolean).join(" - ")} · {q.currency}
        </div>
        <div className="text-[15px] font-medium">{q.name} ({q.symbol})</div>
        <Flash value={price} className="num mt-1 inline-block text-[26px] font-semibold leading-none">
          {num(price)}
        </Flash>
        <div className="mt-1 flex flex-wrap items-baseline gap-x-2">
          <Flash value={change} className={`num text-[14px] ${up ? "text-gain" : "text-loss"}`}>
            {up ? "+" : ""}{num(change)} ({up ? "+" : ""}{num(changePct)}%)
          </Flash>
          {/* When this price was struck. A quote panel with no time on it looks
              identical whether it is a second old or an hour — the same reason
              the live ticks carry their age. */}
          {(tick?.at || q.as_of) && (
            <span className="text-[12px] text-muted-foreground">
              As of {new Date(tick?.at || q.as_of!).toLocaleTimeString("en-US", {
                timeZone: "America/New_York", hour: "numeric", minute: "2-digit", second: "2-digit",
              })} ET
            </span>
          )}
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
      <Row label="Earnings Date" value={q.earnings_date ?? "—"} />
      <Row
        label="Forward Dividend & Yield"
        value={q.dividend_rate
          ? `${num(q.dividend_rate)} (${num(q.dividend_yield)}%)`
          : q.dividend_yield ? `${num(q.dividend_yield)}%` : "—"}
      />
      <Row label="Ex-Dividend Date" value={q.ex_dividend_date ?? "—"} />
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
      <div className="sticky top-0 z-10 flex items-center bg-panel px-[12px] py-[10px] text-[10px] font-medium uppercase tracking-[1px] text-muted-foreground">
        <span className="w-[64px] shrink-0">Symbol</span>
        <span className="min-w-0 flex-1 text-right">Price</span>
        <span className="w-[84px] text-right">1D</span>
      </div>
      {rows.map(r => {
        const up = (r.change_pct ?? 0) >= 0
        return (
          <Link
            key={r.symbol}
            to={`/analysis?symbol=${encodeURIComponent(r.symbol)}`}
            className="row-rule flex h-[44px] items-center px-[12px] text-[14px] hover:bg-muted/60"
          >
            {/* Symbol is plain foreground at normal weight — theirs is not a
                link colour and not bold. Colour in a grid is reserved for
                direction, so spending it on every ticker spends it on nothing. */}
            <span className="w-[64px] shrink-0 truncate">{r.symbol}</span>
            {/* Price over change, in ONE cell, the way theirs stacks them.
                The company name that used to sit here is gone: this tile is a
                third of the board, and the column it had left truncated every
                name to one or two characters — "Direxion Shares ETF Trust…"
                rendered as "D". A column that can only ever show an initial is
                width spent on nothing, and dropping it is what buys the price
                and its change enough room to sit together. */}
            <span className="flex min-w-0 flex-1 flex-col items-end leading-tight">
              <Flash value={r.price} className="tnum">{r.price?.toFixed(2) ?? "—"}</Flash>
              <span className={`tnum text-[13px] ${up ? "text-gain" : "text-loss"}`}>
                {r.change_pct == null ? "—" : `${up ? "+" : ""}${r.change_pct.toFixed(2)}%`}
              </span>
            </span>
            {/* Tinted by direction, same as the change — the line and the
                number must never disagree about which way the day went. */}
            <span className={`flex w-[84px] justify-end pl-2 ${up ? "text-gain" : "text-loss"}`}>
              <Sparkline points={r.spark} baseline dot />
            </span>
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
      span={4}
      title="Stock Movers"
      subtitle="≥ $5 and ≥ $1M turnover — a percentage screen without a liquidity floor is all pumps"
      scroll={TILE_BODY_HEIGHT}
    >
      <div className="flex gap-1 px-3 pb-2">
        {TABS.map(t => (
          <button
            key={t.id}
            onClick={() => setTab(t.id)}
            className={`rounded-md px-2.5 py-1 text-[14px] transition-colors ${
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
