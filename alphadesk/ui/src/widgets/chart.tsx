import { useCallback, useEffect, useState } from "react"
import { useSearchParams } from "react-router-dom"
import { api, type ChartRange, type ChartSeries } from "@/lib/api"
import { PriceChart, type SeriesKind } from "@/components/PriceChart"
import { ChartRanges, ChartToolbar } from "@/components/ChartToolbar"
import { DrawingToolbar, type Drawing, type Tool } from "@/components/ChartDrawings"
import { useIsDark } from "@/lib/theme"
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
  const dark = useIsDark()
  const [params] = useSearchParams()
  const symbol = (params.get("symbol") || "").toUpperCase()

  const [data, setData] = useState<ChartSeries | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [range, setRange] = useState<ChartRange>("1D")
  const [type, setType] = useState<SeriesKind>("line")
  const [logScale, setLogScale] = useState(false)
  const [overlays, setOverlays] = useState<OverlayId[]>([])
  const [panes, setPanes] = useState(false)
  const [expanded, setExpanded] = useState(false)
  // Drawings live here, not inside PriceChart: that component is torn down and
  // rebuilt on every range or series change, and annotations must outlive both.
  const [drawings, setDrawings] = useState<Drawing[]>([])
  const [tool, setTool] = useState<Tool>("none")
  const [drawOpen, setDrawOpen] = useState(false)
  const [drawVisible, setDrawVisible] = useState(true)

  const load = useCallback((sym: string, r: ChartRange) => {
    if (!sym) return
    setErr(null)
    api.chartRange(sym, r).then(setData).catch(e => { setData(null); setErr(String(e.message ?? e)) })
  }, [])

  // eslint-disable-next-line react-hooks/exhaustive-deps -- follow the scoped
  // symbol and the chosen range, not this component's own identity
  useEffect(() => { setData(null); load(symbol, range) }, [symbol, range])

  if (!symbol) {
    return (
      <Widget span={8} title="Chart" scroll={TILE_BODY_HEIGHT}>
        <Empty>Pick a symbol to scope this board — use the chip in the header, or a movers row.</Empty>
      </Widget>
    )
  }

  const showPanes = expanded && panes
  const priceHeight = expanded ? (showPanes ? 360 : 620) : COLLAPSED

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
        interval={data?.interval}
        logScale={logScale} onLogScale={setLogScale}
        overlays={overlays} onOverlays={setOverlays}
        panes={panes} onPanes={setPanes}
        expanded={expanded} onExpand={() => setExpanded(e => !e)}
        drawOpen={drawOpen} onDrawOpen={() => setDrawOpen(o => !o)}
        indicatorsReliable={data?.indicators_reliable ?? false}
      />
      {err && <Empty>{err}</Empty>}
      {!err && !data && <Empty>loading…</Empty>}
      {!err && data && (
        <div className="relative px-[12px] pt-1">
          <PriceChart
            data={data}
            dark={dark}
            compact={!showPanes}
            height={priceHeight}
            series={type}
            overlays={overlays}
            logScale={logScale}
            showIndicatorPanes={showPanes}
            drawings={drawings}
            onDrawingsChange={setDrawings}
            tool={drawOpen ? tool : "none"}
            onToolDone={() => setTool("none")}
            drawingsVisible={drawVisible}
          />
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
