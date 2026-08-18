import { useCallback, useEffect, useState } from "react"
import { useSearchParams } from "react-router-dom"
import { api, type ChartSeries } from "@/lib/api"
import { PriceChart } from "@/components/PriceChart"
import { useIsDark } from "@/lib/theme"
import { Badge, Btn, Widget, fieldCls } from "@/components/terminal"

/** Where a human decides. Chart + indicators on the left, the booking form on
 * the right. Booked trades go into the same ledger as the bot's with
 * trigger_src="HUMAN", pick up the same automated exit management, and get
 * graded forward against SPY on identical terms.
 *
 * Accepts ?symbol=XYZ so the Screener page can hand off a candidate directly
 * — "here's where to look" (Screener) → "now decide" (this page) is meant to
 * be one click, not a re-typed ticker. */
export default function ChartPage() {
  const dark = useIsDark()
  const [params] = useSearchParams()
  const initial = (params.get("symbol") || "AAPL").toUpperCase()
  const [symbol, setSymbol] = useState("")
  const [query, setQuery] = useState(initial)
  const [data, setData] = useState<ChartSeries | null>(null)
  const [days, setDays] = useState(2)
  const [err, setErr] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)

  const load = useCallback((sym: string, d: number) => {
    if (!sym) return
    setLoading(true)
    setErr(null)
    api.chart(sym, d)
      .then(s => { setData(s); setSymbol(s.symbol) })
      .catch(e => { setData(null); setErr(String(e.message ?? e)) })
      .finally(() => setLoading(false))
  }, [])

  // eslint-disable-next-line react-hooks/exhaustive-deps -- only re-run when the URL's
  // ?symbol= changes (a fresh handoff from Screener), not on every `load` identity change
  useEffect(() => { load(initial, 2) }, [initial])

  return (
    <div className="collage">
      <Widget
        span={12}
        title="Chart"
        subtitle="candles + RSI-9 + MACD · indicators hide themselves when the feed is too sparse to trust"
      >
      <form
        className="flex flex-wrap items-center gap-1.5 border-b border-border p-1"
        onSubmit={e => { e.preventDefault(); load(query.trim().toUpperCase(), days) }}
      >
        <input
          value={query}
          onChange={e => setQuery(e.target.value)}
          placeholder="Symbol"
          className={`${fieldCls} w-24 font-mono uppercase`}
        />
        <select
          value={days}
          onChange={e => { const d = Number(e.target.value); setDays(d); load(symbol || query, d) }}
          className={`${fieldCls} w-auto`}
        >
          <option value={1}>1 day</option>
          <option value={2}>2 days</option>
          <option value={5}>5 days</option>
          <option value={10}>10 days</option>
        </select>
        <Btn type="submit" variant="accent" disabled={loading}>
          {loading ? "Loading…" : "Load"}
        </Btn>
        {err && <span className="text-[11px] text-loss">{err}</span>}
      </form>

      {data && (
        <>
          <DataQuality data={data} />
          <div className="grid">
            <div className="min-w-0 p-1">
              <PriceChart data={data} dark={dark} />
            </div>
          </div>
        </>
      )}
      </Widget>
    </div>
  )
}

/** The honesty layer. IEX prints only a fraction of consolidated volume, so a
 * thin name's chart looks normal while its indicators are computed on a sparse,
 * irregular series. Say so loudly rather than let a real decision rest on it. */
function DataQuality({ data }: { data: ChartSeries }) {
  const ok = data.indicators_reliable
  return (
    <div
      className={`border-b px-2 py-1 text-[11px] ${
        ok
          ? "border-border bg-gain/5"
          : "border-amber-600/40 bg-amber-600/10"
      }`}
    >
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1">
        <Badge variant={ok ? "secondary" : "destructive"}>
          {ok ? "Data OK" : "Sparse data"}
        </Badge>
        <span className="font-mono text-xs text-muted-foreground">
          {data.bar_count} bars · {data.sessions} sessions ·{" "}
          {(data.coverage * 100).toFixed(0)}% of a full session ·{" "}
          median gap {data.median_gap_min ?? "—"}m
        </span>
      </div>
      {!ok && (
        <p className="mt-1.5 text-xs text-amber-700 dark:text-amber-500">
          RSI and MACD are <strong>hidden</strong> for this symbol. The IEX feed has too
          few prints here, so a “1-minute” indicator would really be a handful of
          samples spread over hours — it would render like a normal chart and mislead
          you. Price candles are still real; treat gaps as missing data, not flat trade.
        </p>
      )}
    </div>
  )
}

