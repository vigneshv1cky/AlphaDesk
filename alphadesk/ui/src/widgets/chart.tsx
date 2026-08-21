import { useEffect, useMemo, useState } from "react"
import { useSearchParams } from "react-router-dom"
import { type ChartRange } from "@/lib/api"
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
import { useChartSeries } from "@/lib/queries"
import { useLiveTrade } from "@/lib/live"
import { registerWidget } from "@/widgets/registry"
import { TILE_BODY_HEIGHT } from "@/widgets/tile"
import type { OverlayId, PaneId } from "@/lib/indicators"

/** The chart tile on the Markets board.
 *
 * Scoped by ?symbol= like every other tile, so one chip re-points the chart,
 * the quote panel and the AI rail together.
 *
 * Expanding takes the tile to the full width of the board. It is NOT what
 * makes room for indicators: panes used to require it, which meant ticking RSI
 * on a closed tile did nothing, and auto-opening the tile to compensate turned
 * one menu click into the chart taking over the board — then hid the panes
 * again the moment it was closed. A pane is simply drawn, and the tile grows
 * by exactly the height the panes need. Adding one costs a taller tile, which
 * is the honest price and is reversible by removing it; it does not cost the
 * rest of the board.
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

/** How tall a pane needs to be depends on how much of itself it uses.
 *
 * A FITTED pane — MACD, CCI, ATR, OBV — is scaled to its own data, so it
 * fills whatever box it is given and 90px shows the shape fine. A pane with
 * a FIXED scale only ever occupies the slice its readings fall in, and the
 * rest is empty axis: measured on 1,558 bars of NVDA, the middle 90% of
 * RSI-9 sits between 25 and 72, which is 46% of the 0-100 range and about
 * 42px of a 90px pane. That is why the bounded ones read flat.
 *
 * The fixed range is not the thing to change — auto-scaling RSI would put 45
 * at the top of the pane and make a neutral reading look extreme, which is
 * the whole reason it is pinned. So the HEIGHT gives way instead, and the
 * pane itself decides which it is: anything declaring a range gets the
 * taller box. No second list of which indicators are bounded to fall out of
 * step with the panes.
 */
const FITTED_PANE_H = 90
const BOUNDED_PANE_H = 140
const sized = (p: Pane | null): Pane | null =>
  p && { ...p, height: p.range ? BOUNDED_PANE_H : FITTED_PANE_H }

function MarketChart() {
  const [params] = useSearchParams()
  const symbol = (params.get("symbol") || "").toUpperCase()

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

  /** Polled, shared and de-duplicated like every other endpoint on the board
   * (lib/queries). This replaced a hand-rolled fetch whose request-id guard,
   * keep-the-old-series-while-loading and loading flag were all reimplementing
   * what the query layer already does — and which, being one-shot, left the
   * chart frozen at whatever moment the page was opened. */
  const { data, isFetching, error } = useChartSeries(symbol, range, wantedInterval)
  const err = error ? String((error as Error).message ?? error) : null

  // Adopt whatever the server chose, so the toolbar reports the series
  // actually on screen. No refetch loop: `wantedInterval` stays null while
  // unpinned, so this changes no query key.
  useEffect(() => {
    if (wantedInterval == null && data?.interval) setInterval(data.interval)
  }, [wantedInterval, data?.interval])

  /** The live edge, pushed. The polled series owns the bars; a trade only
   * moves the one still forming, so the right edge and the price tag track the
   * market between refreshes without the history ever being invented here.
   *
   * The array LENGTH never changes, which is what keeps this from disturbing
   * the reader: the canvas resets its view on series identity and follows
   * growth, and a tick is neither. */
  const { tick, live } = useLiveTrade(symbol)
  const bars = useMemo(() => {
    const src = data?.bars
    if (!src?.length || !tick || tick.stale || tick.symbol !== symbol) return src ?? []
    const last = src[src.length - 1]
    if (tick.price === last.c) return src
    return [...src.slice(0, -1), {
      ...last,
      c: tick.price,
      h: Math.max(last.h, tick.price),
      l: Math.min(last.l, tick.price),
    }]
  }, [data?.bars, tick, symbol])

  const priceHeight = expanded ? 620 : COLLAPSED


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
      volumePane(bars, Math.round(priceHeight * 0.22), theme.gain, theme.loss),
    ]
    if (data.indicators_reliable) {
      // Built in the order the menu lists them, not the order they were
      // clicked, so the stack does not reshuffle as you toggle.
      for (const id of PANE_INDICATORS.map(p => p.id).filter(id => panes.includes(id))) {
        if (id === "rsi") {
          out.push(sized(rsiPane(bars, data.rsi_9, FITTED_PANE_H, "#7c3aed", {
            oversold: data.thresholds.rsi_oversold, overbought: data.thresholds.rsi_overbought,
          })))
        } else if (id === "macd") {
          out.push(sized(macdPane(bars, data.macd, data.macd_signal, data.macd_hist,
                                  FITTED_PANE_H, "#2563eb", "#f59e0b", theme.gain, theme.loss)))
        } else {
          out.push(sized(oscillatorPane(id, bars, FITTED_PANE_H, theme)))
        }
      }
    }
    return out.filter(Boolean) as Pane[]
  }, [data, bars, panes, priceHeight, theme])

  /** The canvas GROWS with the panes instead of the price pane paying for them.
   * Panes are subtracted from the canvas height inside the renderer, so a fixed
   * height meant each new oscillator ate the chart it was meant to annotate —
   * eight of them would have left the 40px floor. Only when expanded, where the
   * tile has no fixed body height and can take it. */
  const oscHeight = stacked
    .filter(p => p.id !== "volume")
    .reduce((n, p) => n + p.height, 0)
  const canvasHeight = priceHeight + oscHeight

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
      subtitle={data ? `${data.bar_count} bars · ${data.sessions} sessions${live ? " · live" : ""}${isFetching ? " · updating…" : ""}` : undefined}
      scroll={expanded || oscHeight ? undefined : TILE_BODY_HEIGHT}
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
        available={data?.intervals}
        overlays={overlays} onOverlays={setOverlays}
        panes={panes} onPanes={setPanes}
        drawOpen={drawOpen} onDrawOpen={() => setDrawOpen(o => !o)}
        indicatorsReliable={data?.indicators_reliable ?? false}
      />
      {err && <Empty>{err}</Empty>}
      {!err && !data && <Empty>loading…</Empty>}
      {!err && data && (
        <div className="relative px-[12px] pt-1">
          <OhlcvStrip bar={hovered ?? bars[bars.length - 1] ?? null}
                      live={hovered == null} symbol={data.symbol}
                      first={bars[0] ?? null} at={hoverAt} />
          <div className="relative">
            <ChartCanvas
              bars={bars}
              kind={type}
              scale={scale}
              height={canvasHeight}
              panes={stacked}
              overlays={buildOverlays(bars, overlays)}
              onProjection={setProjection}
              onHover={(b, at) => { setHovered(b); setHoverAt(at) }}
              // The SERIES identity, not the bars. A poll brings a new array
              // for the same series and must not reset the reader's view.
              seriesId={`${symbol}:${range}:${data?.interval ?? ""}`}
              onRemovePane={id => setPanes(p => p.filter(x => x !== id))}
              // Session shading only means something on intraday bars — a
              // daily bar IS a session, so marking its boundaries would draw a
              // divider between every pair of bars.
              intraday={!!data?.interval && !["1d", "1wk", "1mo"].includes(data.interval)}
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
