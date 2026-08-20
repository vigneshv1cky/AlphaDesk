import { useState } from "react"
import { Link } from "react-router-dom"
import { useQuote } from "@/lib/queries"
import { normalize, useWatchlist } from "@/lib/watchlist"
import { Btn, Empty, Widget, fieldCls } from "@/components/terminal"

/** The symbols you are following.
 *
 * Not holdings. AlphaDesk books nothing and owns nothing, so there is no cost
 * basis to show and no P&L to compute — claiming otherwise would be the one
 * kind of number this terminal must never invent. What it can honestly tell
 * you is what these names are doing right now, which is what a watchlist is
 * for.
 *
 * The list lives in this browser (see lib/watchlist).
 */

/** One row = one live quote. Quotes are per-symbol requests, so this is a row
 * component rather than a batch: a watchlist is a handful of names, and the
 * shared query cache means a symbol open on Analysis costs nothing here. */
function Row({ symbol, onRemove }: { symbol: string; onRemove: () => void }) {
  const { data, isPending } = useQuote(symbol)
  const chg = data?.change_pct ?? null
  const up = (chg ?? 0) >= 0
  return (
    <tr className="hover:bg-muted/50">
      <td className="px-3 py-[6px]">
        <Link
          to={`/analysis?symbol=${encodeURIComponent(symbol)}`}
          className="num text-[14px] font-semibold text-accent hover:underline"
        >
          {symbol}
        </Link>
      </td>
      <td className="max-w-[240px] truncate px-3 py-[6px] text-[14px] text-muted-foreground">
        {isPending ? "…" : data?.name ?? ""}
      </td>
      <td className="num px-[12px] py-[6px] text-right text-[14px]">
        {data?.price == null ? "—" : data.price.toFixed(2)}
      </td>
      <td className={`num px-[12px] py-[6px] text-right text-[14px] ${
        chg == null ? "text-muted-foreground" : up ? "text-gain" : "text-loss"
      }`}>
        {chg == null ? "—" : `${up ? "+" : ""}${chg.toFixed(2)}%`}
      </td>
      <td className="px-3 py-[6px] text-right">
        <button
          type="button"
          onClick={onRemove}
          aria-label={`Stop watching ${symbol}`}
          className="px-1 text-[15px] leading-none text-muted-foreground hover:text-loss"
        >
          ×
        </button>
      </td>
    </tr>
  )
}

export default function PortfolioPage() {
  const { symbols, add, remove } = useWatchlist()
  const [draft, setDraft] = useState("")

  return (
    <div className="collage">
      <Widget
        span={12}
        title="My Portfolio"
        subtitle="the symbols you follow · saved in this browser — AlphaDesk holds no positions"
        scroll="calc(100vh - 150px)"
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
            className={`${fieldCls} w-32 font-mono uppercase`}
          />
          <Btn type="submit" variant="accent" disabled={!normalize(draft)}>Add</Btn>
          <span className="text-[14px] text-muted-foreground">
            {symbols.length} {symbols.length === 1 ? "symbol" : "symbols"}
          </span>
        </form>

        {symbols.length === 0 ? (
          <Empty>
            nothing on the list yet — add a symbol above, or star one from Analysis
          </Empty>
        ) : (
          <table className="w-full min-w-[520px] border-collapse">
              <thead>
                <tr className="text-[10px] font-medium uppercase tracking-[1px] text-muted-foreground">
                  <th className="sticky top-0 z-10 bg-panel px-[12px] py-[14px] text-left font-normal">Symbol</th>
                  <th className="sticky top-0 z-10 bg-panel px-[12px] py-[14px] text-left font-normal" />
                  <th className="sticky top-0 z-10 bg-panel px-[12px] py-[14px] text-right font-normal">Price</th>
                  <th className="sticky top-0 z-10 bg-panel px-[12px] py-[14px] text-right font-normal">Chg %</th>
                  <th className="sticky top-0 z-10 bg-panel px-[12px] py-[14px] text-right font-normal" />
                </tr>
              </thead>
              <tbody>
                {symbols.map(s => (
                  <Row key={s} symbol={s} onRemove={() => remove(s)} />
                ))}
              </tbody>
          </table>
        )}
      </Widget>
    </div>
  )
}
