import { useState } from "react"
import { useSearchParams } from "react-router-dom"
import type { Quote } from "@/lib/api"
import { useQuotes } from "@/lib/queries"
import { normalize, useWatchlist } from "@/lib/watchlist"
import { Btn, Empty, Flash, Widget, fieldCls } from "@/components/terminal"
import { SymbolChart } from "@/components/SymbolChart"
import { SymbolNews } from "@/components/SymbolNews"

/** The symbols you are following.
 *
 * NOT HOLDINGS, and this is the one page where that distinction is the whole
 * design. AlphaDesk books nothing and owns nothing, so there is no cost basis
 * to show and no P&L to compute. Their portfolio view has both because it sits
 * on an account; ours cannot, and inventing them would be the one kind of
 * number this terminal must never produce. What it can honestly say is what
 * these names are doing right now — so the columns match their table exactly
 * up to the point where position data would start, and then stop.
 *
 * The list lives in this browser (see lib/watchlist).
 *
 * Priced in ONE request. Each row fetching its own quote is the pattern that
 * made the theme pages throttle and return 404s for a third of their rows at
 * random.
 */
const money = (n: number | null | undefined, d = 2) =>
  n == null ? "—" : n.toLocaleString(undefined, { minimumFractionDigits: d, maximumFractionDigits: d })
const compact = (n: number | null | undefined): string => {
  if (n == null) return "—"
  const a = Math.abs(n)
  if (a >= 1e12) return `${(n / 1e12).toFixed(2)}T`
  if (a >= 1e9) return `${(n / 1e9).toFixed(2)}B`
  if (a >= 1e6) return `${(n / 1e6).toFixed(1)}M`
  if (a >= 1e3) return `${(n / 1e3).toFixed(1)}K`
  return n.toFixed(2)
}

function Row({ symbol, data, loading, active, onPick, onRemove }: {
  symbol: string
  data: Quote | null | undefined
  loading: boolean
  active: boolean
  onPick: () => void
  onRemove: () => void
}) {
  const chg = data?.change_pct ?? null
  const up = (chg ?? 0) >= 0
  return (
    <tr
      onClick={onPick}
      aria-selected={active}
      className={`row-rule cursor-pointer ${active ? "bg-muted" : "hover:bg-muted/50"}`}
    >
      <td className="px-3 py-[6px] text-[14px]"><span className="num font-semibold">{symbol}</span></td>
      <td className="max-w-[220px] truncate px-3 py-[6px] text-[14px] text-muted-foreground">
        {loading ? "…" : data?.name ?? ""}
      </td>
      <td className="tnum px-3 py-[6px] text-right text-[14px]">
        <Flash value={data?.price}>{money(data?.price)}</Flash>
      </td>
      <td className={`tnum px-3 py-[6px] text-right text-[14px] ${
        chg == null ? "text-muted-foreground" : up ? "text-gain" : "text-loss"}`}>
        {chg == null ? "—" : `${up ? "+" : ""}${chg.toFixed(2)}%`}
      </td>
      <td className="tnum px-3 py-[6px] text-right text-[14px] text-muted-foreground">
        {compact(data?.volume)}
      </td>
      <td className="tnum px-3 py-[6px] text-right text-[14px] text-muted-foreground">
        {data?.week52_low == null || data?.week52_high == null
          ? "—" : `${money(data.week52_low)} – ${money(data.week52_high)}`}
      </td>
      <td className="tnum px-3 py-[6px] text-right text-[14px]">{compact(data?.market_cap)}</td>
      <td className="px-3 py-[6px] text-right">
        <button
          type="button"
          // Stops the click reaching the row, or removing a symbol would also
          // select it on the way out.
          onClick={e => { e.stopPropagation(); onRemove() }}
          aria-label={`Stop watching ${symbol}`}
          className="px-1 text-[15px] leading-none text-muted-foreground hover:text-loss"
        >
          ×
        </button>
      </td>
    </tr>
  )
}

const TH = "sticky top-0 z-10 bg-panel px-3 py-[14px] font-medium"

export default function PortfolioPage() {
  const { symbols, add, remove } = useWatchlist()
  const [draft, setDraft] = useState("")
  const [params, setParams] = useSearchParams()

  const quotes = useQuotes(symbols)
  const priced = quotes.data?.quotes
  const picked = normalize(params.get("symbol") || "") || symbols[0] || ""
  const pick = (sym: string) => {
    const next = new URLSearchParams(params)
    next.set("symbol", sym)
    setParams(next, { replace: true })
  }

  return (
    <div className="collage">
      <Widget
        span={12}
        title="My Portfolio"
        subtitle="symbols you follow · no positions, no cost basis"
        scroll={340}
        bodyClassName="overflow-x-auto"
      >
        <form
          className="flex flex-wrap items-center gap-1.5 border-b border-border p-1"
          onSubmit={e => { e.preventDefault(); add(draft); setDraft("") }}
        >
          <input
            value={draft}
            onChange={e => setDraft(e.target.value)}
            placeholder="Add a symbol"
            aria-label="Add a symbol to the watchlist"
            className={`${fieldCls} w-32 uppercase tracking-[0.04em]`}
          />
          <Btn type="submit" variant="accent" disabled={!normalize(draft)}>Add</Btn>
          <span className="text-[14px] text-muted-foreground">
            {symbols.length} {symbols.length === 1 ? "symbol" : "symbols"}
          </span>
        </form>

        {symbols.length === 0 ? (
          <Empty>nothing on the list yet — add a symbol above, or star one from Analysis</Empty>
        ) : (
          <table className="w-full min-w-[760px] border-collapse">
            <thead>
              <tr className="text-[10px] uppercase tracking-[1px] text-muted-foreground">
                <th className={`${TH} text-left`}>Symbol</th>
                <th className={`${TH} text-left`}>Name</th>
                <th className={`${TH} text-right`}>Price</th>
                <th className={`${TH} text-right`}>Chg %</th>
                <th className={`${TH} text-right`}>Volume</th>
                <th className={`${TH} text-right`}>52w Range</th>
                <th className={`${TH} text-right`}>Mkt Cap</th>
                <th className={TH} />
              </tr>
            </thead>
            <tbody>
              {symbols.map(s => (
                <Row
                  key={s}
                  symbol={s}
                  data={priced?.[s]}
                  loading={quotes.isPending}
                  active={s === picked}
                  onPick={() => pick(s)}
                  onRemove={() => remove(s)}
                />
              ))}
            </tbody>
          </table>
        )}
      </Widget>

      {picked && <SymbolChart symbol={picked} span={8} />}
      {picked && <SymbolNews symbol={picked} span={4} scroll={420} />}
    </div>
  )
}
