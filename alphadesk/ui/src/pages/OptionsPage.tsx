import { useEffect, useState } from "react"
import { useSearchParams } from "react-router-dom"
import type { OptionRow } from "@/lib/api"
import { useOptionChain, useOptionExpirations, useQuote } from "@/lib/queries"
import { Btn, Empty, Widget, fieldCls } from "@/components/terminal"
import { normalize } from "@/lib/watchlist"

/** One underlying's option chain, one expiry at a time.
 *
 * Read-only, like everything else here — this shows what the book says, it does
 * not price a contract, score a spread or suggest one. AlphaDesk books nothing.
 *
 * There is no IV column and no greeks column because the feed returns both as
 * null on this entitlement (verified against NVDA). A column of dashes would
 * read as "this symbol has no IV" rather than "this feed does not carry it".
 *
 * Rows stay in STRIKE order. A chain is a price ladder; sorting it by volume or
 * moneyness destroys the only structure it has.
 */
const num = (n: number | null | undefined, d = 2) =>
  n == null ? "—" : n.toFixed(d)
const compact = (n: number | null | undefined): string => {
  if (n == null) return "—"
  const a = Math.abs(n)
  if (a >= 1e6) return `${(n / 1e6).toFixed(1)}M`
  if (a >= 1e3) return `${(n / 1e3).toFixed(1)}K`
  return String(n)
}

function Side({ title, rows, spot, itm }: {
  title: string
  rows: OptionRow[]
  spot: number | null
  /** Given a strike, is this contract in the money? Calls and puts answer it
   * in opposite directions, so the caller supplies it. */
  itm: (strike: number, spot: number) => boolean
}) {
  return (
    <Widget span={6} title={title} subtitle={`${rows.length} strikes`} scroll={460}
            bodyClassName="overflow-x-auto">
      {!rows.length ? <Empty>no {title.toLowerCase()} listed for this expiry</Empty> : (
        <table className="w-full border-collapse text-[14px]">
          <thead>
            <tr className="text-[10px] font-medium uppercase tracking-[1px] text-muted-foreground">
              <th className="sticky top-0 z-10 bg-panel px-3 py-[10px] text-left font-medium">Strike</th>
              <th className="sticky top-0 z-10 bg-panel px-3 py-[10px] text-right font-medium">Bid</th>
              <th className="sticky top-0 z-10 bg-panel px-3 py-[10px] text-right font-medium">Ask</th>
              <th className="sticky top-0 z-10 bg-panel px-3 py-[10px] text-right font-medium">Mid</th>
              <th className="sticky top-0 z-10 bg-panel px-3 py-[10px] text-right font-medium">Last</th>
              <th className="sticky top-0 z-10 bg-panel px-3 py-[10px] text-right font-medium">OI</th>
            </tr>
          </thead>
          <tbody>
            {rows.map(r => {
              const inMoney = spot != null && itm(r.strike, spot)
              return (
                <tr key={r.symbol} className={`row-rule ${inMoney ? "bg-muted/40" : ""}`}>
                  <td className="tnum px-3 py-[6px] font-medium">{num(r.strike)}</td>
                  <td className="tnum px-3 py-[6px] text-right">{num(r.bid)}</td>
                  <td className="tnum px-3 py-[6px] text-right">{num(r.ask)}</td>
                  <td className="tnum px-3 py-[6px] text-right text-muted-foreground">{num(r.mid)}</td>
                  <td className="tnum px-3 py-[6px] text-right">{num(r.last)}</td>
                  <td className="tnum px-3 py-[6px] text-right text-muted-foreground">
                    {compact(r.open_interest)}
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      )}
    </Widget>
  )
}

export default function OptionsPage() {
  const [params, setParams] = useSearchParams()
  const symbol = normalize(params.get("symbol") || "") || "NVDA"
  const [query, setQuery] = useState(symbol)
  const [expiry, setExpiry] = useState<string>("")

  useEffect(() => { setQuery(symbol) }, [symbol])

  const exp = useOptionExpirations(symbol)
  const expiries = exp.data?.expirations ?? []
  // Default to the nearest expiry, and reset when the symbol changes — an
  // expiry from the previous underlying may not be listed for this one.
  const active = expiry && expiries.includes(expiry) ? expiry : expiries[0] ?? ""
  useEffect(() => { setExpiry("") }, [symbol])

  const chain = useOptionChain(symbol, active)
  const quote = useQuote(symbol)
  const spot = quote.data?.price ?? null

  const scope = (raw: string) => {
    const sym = normalize(raw)
    if (!sym) return
    const next = new URLSearchParams(params)
    next.set("symbol", sym)
    setParams(next, { replace: true })
  }

  return (
    <div className="collage">
      <form
        className="col-span-12 flex flex-wrap items-center gap-1.5 border-b border-border pb-2"
        onSubmit={e => { e.preventDefault(); scope(query) }}
      >
        <input
          value={query}
          onChange={e => setQuery(e.target.value)}
          placeholder="Symbol"
          aria-label="Underlying symbol"
          className={`${fieldCls} w-28 font-mono uppercase`}
        />
        <Btn type="submit" variant="accent">Load</Btn>
        <span className="num text-[15px] font-semibold">{symbol}</span>
        {spot != null && <span className="tnum text-[14px] text-muted-foreground">{spot.toFixed(2)}</span>}
        <div className="flex-1" />
        {/* Expiries as a strip rather than a select: a chain is read by
            flipping between nearby dates, and a dropdown hides how many there
            are and how close together they sit. */}
        <div className="flex flex-wrap items-center gap-0.5">
          {expiries.map(d => (
            <button
              key={d}
              type="button"
              onClick={() => setExpiry(d)}
              aria-pressed={d === active}
              className={`rounded px-2 py-[3px] text-[12px] leading-none transition-colors ${
                d === active ? "bg-muted font-medium text-foreground"
                             : "text-muted-foreground hover:bg-muted/60 hover:text-foreground"}`}
            >
              {d.slice(5)}
            </button>
          ))}
        </div>
      </form>

      {exp.isPending ? (
        <Widget span={12} title="Options"><Empty>loading expiries…</Empty></Widget>
      ) : !expiries.length ? (
        <Widget span={12} title="Options">
          <Empty>no listed options for {symbol}</Empty>
        </Widget>
      ) : chain.isPending ? (
        <Widget span={12} title="Options"><Empty>loading chain…</Empty></Widget>
      ) : (
        <>
          <Side title="Calls" rows={chain.data?.calls ?? []} spot={spot}
                itm={(strike, s) => strike < s} />
          <Side title="Puts" rows={chain.data?.puts ?? []} spot={spot}
                itm={(strike, s) => strike > s} />
        </>
      )}
    </div>
  )
}
