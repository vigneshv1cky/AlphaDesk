import { useCallback, useEffect, useState } from "react"
import { useSearchParams } from "react-router-dom"
import {
  api, type ChartRange, type ChartSeries, type Fundamentals,
  type MetricPeriod, type MetricStyle,
} from "@/lib/api"
import { ChartCanvas, type Projection, type ScaleMode, type SeriesKind } from "@/components/chart/ChartCanvas"
import { OhlcvStrip } from "@/components/chart/OhlcvStrip"
import { ChartDrawings } from "@/components/ChartDrawings"
import { useChartTheme } from "@/lib/theme"
import { buildOverlays } from "@/lib/indicators"
import { ChartRanges, ChartToolbar } from "@/components/ChartToolbar"
import { DrawingToolbar, type Drawing, type Tool } from "@/components/ChartDrawings"
import type { ChartBar } from "@/lib/api"
import { Empty, Widget } from "@/components/terminal"
import { registerWidget } from "@/widgets/registry"
import { TILE_BODY_HEIGHT } from "@/widgets/tile"
import type { OverlayId } from "@/lib/indicators"

/** The chart tile on the Markets board.
 *
 * Scoped by ?symbol= like every other tile, so one chip re-points the chart,
 * the quote panel and the AI rail together.
 *
 * Expanding takes the tile to the full width of the board and gives the price
 * pane the height the indicator panes need — which is why RSI and MACD are
 * only offered once expanded. At 440px they would be two 40px slivers, and an
 * indicator you cannot read still invites you to read it.
 */

const COLLAPSED = 318

function MarketChart() {
  const [params] = useSearchParams()
  const symbol = (params.get("symbol") || "").toUpperCase()

  const [data, setData] = useState<ChartSeries | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [range, setRange] = useState<ChartRange>("1D")
  const [type, setType] = useState<SeriesKind>("line")
  const [scale, setScale] = useState<ScaleMode>("linear")
  const [interval, setInterval] = useState("1m")
  const [overlays, setOverlays] = useState<OverlayId[]>([])
  const [panes, setPanes] = useState(false)
  const [expanded, setExpanded] = useState(false)
  // Drawings live here, not inside PriceChart: that component is torn down and
  // rebuilt on every range or series change, and annotations must outlive both.
  const [drawings, setDrawings] = useState<Drawing[]>([])
  const [tool, setTool] = useState<Tool>("none")
  const [drawOpen, setDrawOpen] = useState(false)
  const [drawVisible, setDrawVisible] = useState(true)
  const [fundamentals, setFundamentals] = useState<Fundamentals | null>(null)
  const [metrics, setMetrics] = useState<string[]>([])
  const [metricPeriod, setMetricPeriod] = useState<MetricPeriod>("quarterly")
  const [metricStyle, setMetricStyle] = useState<MetricStyle>("bars")
  const [projection, setProjection] = useState<Projection | null>(null)
  const [hovered, setHovered] = useState<ChartBar | null>(null)
  const [hoverAt, setHoverAt] = useState<{ x: number; y: number } | null>(null)
  const theme = useChartTheme()

  // Statements are fetched per symbol and period, not per range — they do not
  // change when you zoom.
  useEffect(() => {
    if (!symbol) { setFundamentals(null); return }
    let alive = true
    setFundamentals(null)
    api.fundamentals(symbol, metricPeriod)
      .then(d => { if (alive) setFundamentals(d) })
      .catch(() => { if (alive) setFundamentals(null) })
    return () => { alive = false }
  }, [symbol, metricPeriod])

  const load = useCallback((sym: string, r: ChartRange, iv: string) => {
    if (!sym) return
    setErr(null)
    api.chartRange(sym, r, iv).then(setData).catch(e => { setData(null); setErr(String(e.message ?? e)) })
  }, [])

  // eslint-disable-next-line react-hooks/exhaustive-deps -- follow the scoped
  // symbol, range and interval, not this component's own identity
  useEffect(() => { setData(null); load(symbol, range, interval) }, [symbol, range, interval])

  if (!symbol) {
    return (
      <Widget span={8} title="Chart" scroll={TILE_BODY_HEIGHT}>
        <Empty>Pick a symbol to scope this board — use the chip in the header, or a movers row.</Empty>
      </Widget>
    )
  }

  const priceHeight = expanded ? 620 : COLLAPSED

  return (
    <Widget
      span={expanded ? 12 : 8}
      symbol={symbol}
      title="Chart"
      subtitle={data ? `${data.bar_count} bars · ${data.sessions} sessions` : undefined}
      scroll={expanded ? undefined : TILE_BODY_HEIGHT}
    >
      <ChartToolbar
        type={type} onType={setType}
        scale={scale} onScale={setScale}
        interval={interval} onInterval={setInterval}
        servedInterval={data?.interval}
        servedLabel={data?.interval_label}
        overlays={overlays} onOverlays={setOverlays}
        panes={panes} onPanes={setPanes}
        expanded={expanded} onExpand={() => setExpanded(e => !e)}
        drawOpen={drawOpen} onDrawOpen={() => setDrawOpen(o => !o)}
        fundamentals={fundamentals}
        metrics={metrics}
        onToggleMetric={id => setMetrics(m => m.includes(id) ? m.filter(x => x !== id) : [...m, id])}
        metricPeriod={metricPeriod} onMetricPeriod={setMetricPeriod}
        metricStyle={metricStyle} onMetricStyle={setMetricStyle}
        indicatorsReliable={data?.indicators_reliable ?? false}
      />
      {err && <Empty>{err}</Empty>}
      {!err && !data && <Empty>loading…</Empty>}
      {!err && data && (
        <div className="relative px-[12px] pt-1">
          <OhlcvStrip bar={hovered ?? data.bars[data.bars.length - 1] ?? null}
                      live={hovered == null} symbol={data.symbol}
                      first={data.bars[0] ?? null} at={hoverAt} />
          <div className="relative">
            <ChartCanvas
              bars={data.bars}
              kind={type}
              scale={scale}
              height={priceHeight}
              volumeHeight={Math.round(priceHeight * 0.22)}
              overlays={buildOverlays(data.bars, overlays)}
              onProjection={setProjection}
              onHover={(b, at) => { setHovered(b); setHoverAt(at) }}
              {...theme}
            />
            <ChartDrawings
              projection={projection}
              tool={drawOpen ? tool : "none"}
              onToolDone={() => setTool("none")}
              drawings={drawings}
              onChange={setDrawings}
              visible={drawVisible}
              height={priceHeight}
            />
          </div>
          {drawOpen && (
            <DrawingToolbar
              tool={tool} onTool={setTool}
              visible={drawVisible} onVisible={setDrawVisible}
              count={drawings.length}
              onClear={() => setDrawings([])}
              onClose={() => { setDrawOpen(false); setTool("none") }}
            />
          )}
        </div>
      )}
      <ChartRanges range={range} onRange={setRange} />
    </Widget>
  )
}

registerWidget({ id: "market-chart", order: 12, component: MarketChart })
