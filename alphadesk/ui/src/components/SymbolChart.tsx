import { useCallback, useEffect, useState } from "react"
import { api, type ChartBar, type ChartRange, type ChartSeries } from "@/lib/api"
import { ChartCanvas, type Projection } from "@/components/chart/ChartCanvas"
import { OhlcvStrip } from "@/components/chart/OhlcvStrip"
import { useChartTheme } from "@/lib/theme"
import { volumePane } from "@/components/chart/panes"
import { Badge, Widget } from "@/components/terminal"

/** Candles and volume for one symbol, on the Analysis view.
 *
 * The symbol is a prop rather than page state: Analysis owns one input that
 * scopes every panel on it, so two panels can never disagree about which
 * company you are looking at.
 */
const RANGES: ChartRange[] = ["1D", "5D", "1M", "3M", "6M", "YTD", "1Y", "5Y", "MAX"]

export function SymbolChart({ symbol: requested, span = 12 }: {
  symbol: string
  /** Analysis runs the chart at 8 with the quote panel beside it, the way
   * AlphaSpace's analysis view does; the markets board keeps it full width. */
  span?: number
}) {
  const theme = useChartTheme()
  const [data, setData] = useState<ChartSeries | null>(null)
  const [range, setRange] = useState<ChartRange>("5D")
  const [err, setErr] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const [, setProjection] = useState<Projection | null>(null)
  const [hovered, setHovered] = useState<ChartBar | null>(null)
  const [at, setAt] = useState<{ x: number; y: number } | null>(null)

  const load = useCallback((sym: string, r: ChartRange) => {
    if (!sym) return
    setLoading(true); setErr(null)
    api.chartRange(sym, r)
      .then(setData)
      .catch(e => { setData(null); setErr(String(e.message ?? e)) })
      .finally(() => setLoading(false))
  }, [])

  // eslint-disable-next-line react-hooks/exhaustive-deps -- follow the symbol
  // and range, not this component's own identity
  useEffect(() => { if (requested) load(requested, range) }, [requested, range])

  return (
    <Widget span={span} symbol={requested} title="Chart"
      subtitle={data ? `${data.bar_count} bars · ${data.interval_label ?? ""}` : undefined}>
      <div className="flex flex-wrap items-center gap-px border-b border-border px-[12px] py-1">
        {loading && <span className="px-1 text-[12px] text-muted-foreground">loading…</span>}
        {RANGES.map(r => (
          <button key={r} type="button" onClick={() => setRange(r)}
            className={`px-2 py-[3px] text-[12px] tabular-nums transition-colors ${
              range === r ? "bg-accent/15 font-semibold text-accent"
                          : "text-muted-foreground hover:bg-muted hover:text-foreground"}`}>
            {r}
          </button>
        ))}
        {err && <span className="ml-2 text-[12px] text-loss">{err}</span>}
      </div>

      {data && (
        <>
          <DataQuality data={data} />
          <div className="relative px-[12px] pt-1">
            <OhlcvStrip bar={hovered ?? data.bars[data.bars.length - 1] ?? null}
              live={hovered == null} symbol={data.symbol}
              first={data.bars[0] ?? null} at={at} />
            <ChartCanvas
              bars={data.bars} kind="candles" scale="linear"
              height={420}
              panes={[volumePane(data.bars, 96, theme.gain, theme.loss)].filter(Boolean) as never}
              onProjection={setProjection}
              onHover={(b, p) => { setHovered(b); setAt(p) }}
              {...theme}
            />
          </div>
        </>
      )}
    </Widget>
  )
}

/** The honesty layer. A sparse feed's chart looks normal while its indicators
 * are computed on an irregular series — say so rather than let a decision rest
 * on it. */
function DataQuality({ data }: { data: ChartSeries }) {
  const ok = data.indicators_reliable
  return (
    <div className={`border-b px-2 py-1 text-[12px] ${
      ok ? "border-border bg-gain/5" : "border-amber-600/40 bg-amber-600/10"}`}>
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1">
        <Badge variant={ok ? "secondary" : "destructive"}>{ok ? "Data OK" : "Sparse data"}</Badge>
        <span className="tnum text-[12px] text-muted-foreground">
          {data.bar_count} bars · {data.sessions} sessions ·{" "}
          {(data.coverage * 100).toFixed(0)}% of a full session ·{" "}
          median gap {data.median_gap_min ?? "—"}m
        </span>
      </div>
    </div>
  )
}
