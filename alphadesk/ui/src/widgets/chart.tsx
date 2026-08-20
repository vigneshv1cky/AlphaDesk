import { useCallback, useEffect, useState } from "react"
import { useSearchParams } from "react-router-dom"
import { api, type ChartSeries } from "@/lib/api"
import { PriceChart } from "@/components/PriceChart"
import { useIsDark } from "@/lib/theme"
import { Empty, Widget } from "@/components/terminal"
import { registerWidget } from "@/widgets/registry"
import { TILE_BODY_HEIGHT } from "@/widgets/tile"

/** The chart tile on the Markets board.
 *
 * Price and volume only. Their board runs this at 440px across two of three
 * columns, and at that height the RSI and MACD panes would be two 40px slivers
 * — worse than absent, because an indicator you cannot read still invites you
 * to read it. The full three-pane chart lives on Analysis.
 *
 * Scoped by ?symbol= like every other tile, so one chip in the URL re-points
 * the chart, the quote panel and the AI rail together.
 */

const RANGES = [
  { days: 1, label: "1D" },
  { days: 2, label: "2D" },
  { days: 5, label: "5D" },
  { days: 10, label: "10D" },
  { days: 30, label: "1M" },
] as const

function MarketChart() {
  const dark = useIsDark()
  const [params] = useSearchParams()
  const symbol = (params.get("symbol") || "").toUpperCase()
  const [data, setData] = useState<ChartSeries | null>(null)
  const [days, setDays] = useState(2)
  const [err, setErr] = useState<string | null>(null)

  const load = useCallback((sym: string, d: number) => {
    if (!sym) return
    setErr(null)
    api.chart(sym, d)
      .then(setData)
      .catch(e => { setData(null); setErr(String(e.message ?? e)) })
  }, [])

  // eslint-disable-next-line react-hooks/exhaustive-deps -- follow the scoped
  // symbol, not this component's own identity
  useEffect(() => { setData(null); load(symbol, days) }, [symbol])

  if (!symbol) {
    return (
      <Widget span={8} title="Chart" scroll={TILE_BODY_HEIGHT}>
        <Empty>Pick a symbol to scope this board — use the chip in the header, or a movers row.</Empty>
      </Widget>
    )
  }

  return (
    <Widget
      span={8}
      symbol={symbol}
      title="Chart"
      subtitle={data ? `${data.bar_count} bars · ${data.sessions} sessions` : undefined}
      scroll={TILE_BODY_HEIGHT}
    >
      <div className="flex flex-wrap items-center gap-px border-b border-grid-line px-[12px] py-1">
        {RANGES.map(r => (
          <button
            key={r.days}
            type="button"
            onClick={() => { setDays(r.days); load(symbol, r.days) }}
            className={`px-2 py-[3px] text-[12px] tabular-nums transition-colors ${
              days === r.days
                ? "bg-accent/15 font-semibold text-accent"
                : "text-muted-foreground hover:bg-muted hover:text-foreground"
            }`}
          >
            {r.label}
          </button>
        ))}
        {data && !data.indicators_reliable && (
          <span className="ml-2 text-[12px] text-muted-foreground">
            sparse feed — price only
          </span>
        )}
      </div>
      {err && <Empty>{err}</Empty>}
      {!err && !data && <Empty>loading…</Empty>}
      {!err && data && (
        <div className="px-[12px] pt-1">
          <PriceChart data={data} dark={dark} compact height={318} series="line" />
        </div>
      )}
    </Widget>
  )
}

registerWidget({ id: "market-chart", order: 12, component: MarketChart })
