import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { useSearchParams } from "react-router-dom"
import { api, type ChartRange, type ChartSeries } from "@/lib/api"
import { ChartCanvas, type Projection, type ScaleMode, type SeriesKind } from "@/components/chart/ChartCanvas"
import { OhlcvStrip } from "@/components/chart/OhlcvStrip"
import { ChartDrawings } from "@/components/ChartDrawings"
import { useChartTheme } from "@/lib/theme"
import { buildOverlays } from "@/lib/indicators"
import { macdPane, oscillatorPane, rsiPane, volumePane, type Pane } from "@/components/chart/panes"
import { ChartRanges, ChartToolbar } from "@/components/ChartToolbar"
import { DrawingToolbar, type Drawing, type Tool } from "@/components/ChartDrawings"
import type { ChartBar } from "@/lib/api"
import { Empty, Widget } from "@/components/terminal"
import { PANE_INDICATORS } from "@/lib/indicators"
import { registerWidget } from "@/widgets/registry"
import { TILE_BODY_HEIGHT } from "@/widgets/tile"
import type { OverlayId, PaneId } from "@/lib/indicators"

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

/** What the canvas shares its tile with: the toolbar and the range strip (32
 * each) plus the OHLCV readout and its top padding (25).
 *
 * The canvas takes whatever is left, rather than a number picked independently
 * of the tile it has to fit inside. Those two used to be unrelated constants
 * that happened to disagree by five pixels — enough to put a scrollbar on the
 * chart, and a chart you scroll to see the bottom of is not a chart. Deriving
 * it means a change to the tile height cannot silently reintroduce that. */
const CHART_CHROME = 32 + 32 + 25
const COLLAPSED = TILE_BODY_HEIGHT - CHART_CHROME

function MarketChart() {
  const [params] = useSearchParams()
  const symbol = (params.get("symbol") || "").toUpperCase()

  const [data, setData] = useState<ChartSeries | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const [range, setRange] = useState<ChartRange>("1D")
  const [type, setType] = useState<SeriesKind>("line")
  const [scale, setScale] = useState<ScaleMode>("linear")
  const [interval, setInterval] = useState("1m")
  /** Whether the interval was chosen BY HAND. Unpinned, the server picks the
   * one that suits the range and the toolbar adopts it.
   *
   * Carrying a hand-set interval across a range change was quietly expensive:
   * the 1D view defaults to 1-minute bars, so switching to 1Y asked for a year
   * of minute data. The server correctly refuses and serves hourly instead —
   * but a year of HOURLY bars is a 20-second upstream fetch that nothing
   * caches, and the answer is not even the daily series the range wants.
   * Asking for the range alone returns in half a second. */
  const [intervalPinned, setIntervalPinned] = useState(false)
  const wantedInterval = intervalPinned ? interval : null
  const [overlays, setOverlays] = useState<OverlayId[]>([])
  const [panes, setPanes] = useState<PaneId[]>([])
  const [expanded, setExpanded] = useState(false)
  // Drawings live here, not inside PriceChart: that component is torn down and
  // rebuilt on every range or series change, and annotations must outlive both.
  const [drawings, setDrawings] = useState<Drawing[]>([])
  const [tool, setTool] = useState<Tool>("none")
  const [drawOpen, setDrawOpen] = useState(false)
  const [drawVisible, setDrawVisible] = useState(true)
  const [projection, setProjection] = useState<Projection | null>(null)
  const [hovered, setHovered] = useState<ChartBar | null>(null)
  const [hoverAt, setHoverAt] = useState<{ x: number; y: number } | null>(null)
  const theme = useChartTheme()

  /** Fetch WITHOUT throwing the current series away first.
   *
   * Blanking the chart to show a placeholder meant every range change tore the
   * canvas down and put a loading panel in its place — the chart vanished,
   * text appeared where it had been, and the chart came back. Keeping the last
   * series on screen while the next one lands makes a range change a redraw
   * rather than a teardown, and there is nothing to place a layer over.
   *
   * The response is guarded by a request id: now that a stale series stays
   * visible, a slow reply for a range you have already moved off must not be
   * allowed to land on top of a newer one. */
  const reqId = useRef(0)
  const load = useCallback((sym: string, r: ChartRange, iv: string | null) => {
    if (!sym) return
    const id = ++reqId.current
    setErr(null)
    setLoading(true)
    api.chartRange(sym, r, iv ?? undefined)
      .then(d => {
        if (id !== reqId.current) return
        setData(d)
        // Adopt whatever the server chose, so the toolbar reports the series
        // actually on screen. Safe against a refetch loop: `wantedInterval`
        // stays null while unpinned, so this changes no dependency.
        if (iv == null && d.interval) setInterval(d.interval)
      })
      .catch(e => {
        if (id !== reqId.current) return
        setData(null)
        setErr(String(e.message ?? e))
      })
      .finally(() => { if (id === reqId.current) setLoading(false) })
  }, [])

  // A different SYMBOL does clear it: holding one company's bars under another
  // company's name is the one version of this that would actually mislead.
  useEffect(() => { setData(null) }, [symbol])

  // eslint-disable-next-line react-hooks/exhaustive-deps -- follow the scoped
  // symbol, range and interval, not this component's own identity
  useEffect(() => { load(symbol, range, wantedInterval) }, [symbol, range, wantedInterval])

  const priceHeight = expanded ? 620 : COLLAPSED
  const OSC_HEIGHT = 90

  /** Volume always; the oscillators only once expanded, because at tile height
   * they would be 40px slivers and an indicator you cannot read still invites
   * you to read it.
   *
   * Every one of them is gated on `indicators_reliable`, RSI and MACD included.
   * The browser-computed ones read the same bars the server measured, so they
   * inherit the same verdict rather than each forming its own — an oscillator
   * on a feed too sparse to support it draws identically to a real one, which
   * is exactly what makes it dangerous (CLAUDE.md's second invariant). */
  const stacked: Pane[] = useMemo(() => {
    if (!data) return []
    const out: (Pane | null)[] = [
      volumePane(data.bars, Math.round(priceHeight * 0.22), theme.gain, theme.loss),
    ]
    if (expanded && data.indicators_reliable) {
      // Built in the order the menu lists them, not the order they were
      // clicked, so the stack does not reshuffle as you toggle.
      for (const id of PANE_INDICATORS.map(p => p.id).filter(id => panes.includes(id))) {
        if (id === "rsi") {
          out.push(rsiPane(data.bars, data.rsi_9, OSC_HEIGHT, "#7c3aed", {
            oversold: data.thresholds.rsi_oversold, overbought: data.thresholds.rsi_overbought,
          }))
        } else if (id === "macd") {
          out.push(macdPane(data.bars, data.macd, data.macd_signal, data.macd_hist,
                            OSC_HEIGHT, "#2563eb", "#f59e0b", theme.gain, theme.loss))
        } else {
          out.push(oscillatorPane(id, data.bars, OSC_HEIGHT, theme))
        }
      }
    }
    return out.filter(Boolean) as Pane[]
  }, [data, expanded, panes, priceHeight, theme])

  /** The canvas GROWS with the panes instead of the price pane paying for them.
   * Panes are subtracted from the canvas height inside the renderer, so a fixed
   * height meant each new oscillator ate the chart it was meant to annotate —
   * eight of them would have left the 40px floor. Only when expanded, where the
   * tile has no fixed body height and can take it. */
  const canvasHeight = priceHeight + (expanded
    ? stacked.filter(p => p.id !== "volume").reduce((n, p) => n + p.height, 0)
    : 0)

  // The early return comes AFTER every hook. Returning before one makes the
  // hook order differ between renders, which React cannot recover from — lint
  // caught this, not the build, and not the running page.
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
      subtitle={data ? `${data.bar_count} bars · ${data.sessions} sessions${loading ? " · updating…" : ""}` : undefined}
      scroll={expanded ? undefined : TILE_BODY_HEIGHT}
      // Controlled, so the header's ⤢ and the toolbar's drive ONE state. The
      // chart cannot expand on its own terms — the price pane grows and the
      // oscillator panes only appear once there is height to read them — so
      // the widget must not keep a second opinion about whether it is open.
      expanded={expanded}
      onExpandChange={setExpanded}
    >
      <ChartToolbar
        type={type} onType={setType}
        scale={scale} onScale={setScale}
        interval={interval}
        onInterval={iv => { setInterval(iv); setIntervalPinned(true) }}
        servedInterval={data?.interval}
        servedLabel={data?.interval_label}
        overlays={overlays} onOverlays={setOverlays}
        panes={panes} onPanes={setPanes}
        drawOpen={drawOpen} onDrawOpen={() => setDrawOpen(o => !o)}
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
              height={canvasHeight}
              panes={stacked}
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
              // Matches the canvas, not the price pane: this is the surface
              // drawings are placed on, and it covered the whole canvas before
              // panes could grow it. Where a drawing LANDS is the projection's
              // business either way.
              height={canvasHeight}
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
      {/* A range change releases the interval back to the server's choice —
          the two are one decision, and pinning minute bars onto a year is not
          a thing to preserve. */}
      <ChartRanges range={range} onRange={r => { setRange(r); setIntervalPinned(false) }} />
    </Widget>
  )
}

registerWidget({ id: "market-chart", order: 12, component: MarketChart })
