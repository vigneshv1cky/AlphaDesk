import { useEffect, useMemo, useRef, useState } from "react"
import {
  AreaSeries,
  BarSeries,
  CandlestickSeries,
  createChart,
  HistogramSeries,
  LineSeries,
  type IChartApi,
  type ISeriesApi,
  type SeriesType,
  type Time,
  type UTCTimestamp,
} from "lightweight-charts"
import {
  bollinger, dema, ema, envelopes, keltner, OVERLAYS, priceChannel,
  sma, tema, vwap, wma, type OverlayId, type Point,
} from "@/lib/indicators"
import { ChartDrawings, type Drawing, type Tool } from "@/components/ChartDrawings"
import type { ChartBar, ChartSeries, Fundamentals, MetricStyle } from "@/lib/api"

// Distinct accent colors for RSI/MACD/signal — these read fine on both
// themes already, so unlike grid/text/candle colors (which come from the
// shared design tokens below) they stay as plain named literals rather than
// CSS custom properties.
const RSI_COLOR = "#7c3aed"
const MACD_LINE_COLOR = "#2563eb"
const SIGNAL_COLOR = "#f59e0b"

/** Resolve a CSS custom property to a canvas-safe color string. index.css's
 * tokens are oklch() — lightweight-charts draws to <canvas> and validates
 * colors with its own parser, which doesn't understand oklch() in any
 * serialization. Modern Chromium now PRESERVES oklch() notation through both
 * getComputedStyle reads and even a CanvasRenderingContext2D.fillStyle
 * get/set round-trip (it no longer downgrades to legacy rgb() the way older
 * engines did), so neither of those alone produces a safe string. Actually
 * PAINTING the color and reading the rasterized pixel back forces a true
 * conversion, since the canvas backing store is concrete sRGB regardless of
 * the input color space. */
function cssVar(name: string): string {
  const probe = document.createElement("span")
  probe.style.color = `var(${name})`
  document.body.appendChild(probe)
  const resolved = getComputedStyle(probe).color
  document.body.removeChild(probe)
  const canvas = document.createElement("canvas")
  canvas.width = 1
  canvas.height = 1
  const ctx = canvas.getContext("2d")
  if (!ctx) return resolved
  ctx.fillStyle = resolved
  ctx.fillRect(0, 0, 1, 1)
  const [r, g, b, a] = ctx.getImageData(0, 0, 1, 1).data
  return `rgba(${r}, ${g}, ${b}, ${(a / 255).toFixed(3)})`
}

/** The card that follows the crosshair.
 *
 * Theirs shows the date and the symbol's change at that point. "Change" needs a
 * baseline, and the honest one here is the FIRST BAR IN THE SERIES — the change
 * across what you are actually looking at. Using the previous close would be a
 * different claim, and one this component cannot check, since a range longer
 * than a day has no single previous close.
 *
 * Flips to the left of the cursor near the right edge so it never leaves the
 * pane, and never intercepts the mouse.
 */
function CrosshairCard({ bar, first, symbol, at, width }: {
  bar: ChartBar
  first: ChartBar | null
  symbol: string
  at: { x: number; y: number }
  width: number
}) {
  const base = first?.c
  const pct = base ? ((bar.c - base) / Math.abs(base)) * 100 : null
  const up = (pct ?? 0) >= 0
  const flip = at.x > width - 190
  const when = new Date(bar.t).toLocaleString("en-US", {
    timeZone: "America/New_York", month: "short", day: "numeric",
    year: "numeric", hour: "numeric", minute: "2-digit",
  })
  return (
    <div
      className="pointer-events-none absolute z-20 rounded-md border border-border bg-popover/95 px-2.5 py-1.5 shadow-lg backdrop-blur"
      style={{ left: flip ? at.x - 178 : at.x + 14, top: Math.max(4, at.y - 34) }}
    >
      <div className="text-[11px] text-muted-foreground">{when}</div>
      <div className="flex items-center gap-1.5 text-[12px]">
        <span className="h-1.5 w-1.5 rounded-full" style={{ background: "var(--accent)" }} />
        <span className="font-medium">{symbol}</span>
        <span className={`tnum ${pct == null ? "text-muted-foreground" : up ? "text-gain" : "text-loss"}`}>
          {pct == null ? "—" : `${up ? "+" : ""}${pct.toFixed(2)}%`}
        </span>
      </div>
    </div>
  )
}

/** Drop a resolved rgb/rgba string to a given alpha. cssVar() always hands
 * back a concrete rgba() (it rasterizes to get there), so this only has to
 * handle that one shape. */
function fade(color: string, alpha: number): string {
  const parts = color.match(/[\d.]+/g)
  if (!parts || parts.length < 3) return color
  return `rgba(${parts[0]}, ${parts[1]}, ${parts[2]}, ${alpha})`
}

/** Candles + RSI-9 + MACD(12,26,9), in three stacked panes.
 *
 * Indicators are drawn ONLY when the series says they're trustworthy — see
 * `indicators_reliable` in api.ts. A MACD computed on 61 prints spread across
 * five sessions renders exactly like one computed on 1,950 real minute bars,
 * and the whole point of this app is to not fool its own operator. */
export type SeriesKind = "candles" | "line" | "area" | "bars"
export type ScaleMode = "linear" | "log" | "percent"

export function PriceChart({
  data, dark, compact = false, height = 320, series = "candles",
  overlays = [], scale = "linear", showIndicatorPanes = true,
  fundamentals = null, metrics = [], metricStyle = "bars",
  drawings, onDrawingsChange, tool = "none", onToolDone, drawingsVisible = true,
}: {
  data: ChartSeries
  dark: boolean
  /** Price and volume only — no RSI or MACD panes. Their markets tile is
   * 440px tall and shows exactly this; the indicator panes belong on the
   * Analysis view where there is room to read them. */
  compact?: boolean
  height?: number
  /** "line" matches their board exactly — measured: a 1.5px stroke in the
   * accent blue with no fill. Candles carry more per bar and stay the default
   * on Analysis, where the chart is the point rather than one tile of six. */
  series?: SeriesKind
  /** Overlays drawn on the price pane. Chosen, not fixed: the RSI/MACD panes
   * were hardcoded because the retired strategy read them, and a general
   * chart lets the reader pick what to see. */
  overlays?: OverlayId[]
  /** "linear" | "log" | "percent". Percent rebases the pane to the change from
   * the first visible bar, which is the only fair way to compare two names of
   * different price. */
  scale?: ScaleMode
  /** Show the RSI and MACD panes. Ignored in compact mode, which has no room. */
  showIndicatorPanes?: boolean
  /** Fundamentals plotted in their own pane. They cannot share the price axis:
   * revenue is in billions and price in dollars, so one scale would flatten
   * whichever is smaller into the floor. */
  fundamentals?: Fundamentals | null
  metrics?: string[]
  metricStyle?: MetricStyle
  /** Hand-drawn annotations. Passed in rather than owned here so they survive
   * a range change — the chart is torn down and rebuilt on every one of those,
   * and drawings must not go with it. */
  drawings?: Drawing[]
  onDrawingsChange?: (next: Drawing[]) => void
  tool?: Tool
  onToolDone?: () => void
  drawingsVisible?: boolean
}) {
  const priceRef = useRef<HTMLDivElement>(null)
  const rsiRef = useRef<HTMLDivElement>(null)
  const macdRef = useRef<HTMLDivElement>(null)
  // Which bar the OHLCV strip describes. null = not hovering, and the strip
  // falls back to the most recent bar so it is never blank.
  const [hovered, setHovered] = useState<ChartBar | null>(null)
  // Where the crosshair is, in pane pixels — the tooltip card follows it.
  const [at, setAt] = useState<{ x: number; y: number } | null>(null)
  // Held in state so the drawing overlay can project against the live chart.
  const [api, setApi] = useState<{ chart: IChartApi; series: ISeriesApi<SeriesType> } | null>(null)
  const lastBar = data.bars.length ? data.bars[data.bars.length - 1] : null
  const readout = hovered ?? lastBar

  useEffect(() => {
    // Compact mode renders no indicator panes, so those refs are null by
    // design — requiring them here bailed before a chart was ever created.
    if (!priceRef.current) return
    if (!compact && (!rsiRef.current || !macdRef.current)) return

    const text = cssVar("--muted-foreground")
    // Their grid is barely there — rgba(255,255,255,0.06) measured. A chart
    // whose gridlines compete with its series reads as a spreadsheet with a
    // line on it, the same mistake the row rules were. Via a token, not a
    // literal: a hardcoded white-alpha would be invisible on the light theme.
    const grid = cssVar("--chart-grid")
    const gain = cssVar("--gain")
    const loss = cssVar("--loss")
    const base = {
      layout: { background: { color: "transparent" }, textColor: text, attributionLogo: false },
      grid: { vertLines: { color: grid }, horzLines: { color: grid } },
      // lightweight-charts price scale modes: 0 normal, 1 logarithmic,
      // 2 percentage.
      rightPriceScale: { borderColor: grid, mode: scale === "log" ? 1 : scale === "percent" ? 2 : 0 },
      timeScale: { borderColor: grid, timeVisible: true, secondsVisible: false },
      autoSize: true,
    }

    const t = (iso: string) => (Date.parse(iso) / 1000) as UTCTimestamp

    // De-duplicate and sort: lightweight-charts requires strictly ascending,
    // unique timestamps, and minute bars occasionally repeat a stamp.
    const seen = new Set<number>()
    const idx: number[] = []
    data.bars.forEach((b, i) => {
      const ts = Date.parse(b.t) / 1000
      if (!seen.has(ts)) {
        seen.add(ts)
        idx.push(i)
      }
    })

    const priceChart = createChart(priceRef.current, { ...base, height })
    const accent = cssVar("--accent")
    let priceSeries: ISeriesApi<SeriesType> | null = null
    if (series === "line" || series === "area") {
      const kind = series === "area" ? AreaSeries : LineSeries
      const opts = series === "area"
        ? { lineColor: accent, topColor: fade(accent, 0.28), bottomColor: fade(accent, 0.02), lineWidth: 2 as const }
        : { color: accent, lineWidth: 2 as const }
      const line = priceChart.addSeries(kind, opts)
      line.setData(idx.map(i => ({ time: t(data.bars[i].t), value: data.bars[i].c })))
      priceSeries = line
    } else if (series === "bars") {
      const bars = priceChart.addSeries(BarSeries, { upColor: gain, downColor: loss })
      bars.setData(idx.map(i => {
        const b = data.bars[i]
        return { time: t(b.t), open: b.o, high: b.h, low: b.l, close: b.c }
      }))
      priceSeries = bars
    } else {
      const candles = priceChart.addSeries(CandlestickSeries, {
        upColor: gain, downColor: loss,
        wickUpColor: gain, wickDownColor: loss, borderVisible: false,
      })
      candles.setData(idx.map(i => {
        const b = data.bars[i]
        return { time: t(b.t), open: b.o, high: b.h, low: b.l, close: b.c }
      }))
      priceSeries = candles
    }

    // Chosen overlays, drawn over the price series. Each is computed from the
    // bars already on screen (lib/indicators) — no round trip on a toggle.
    if (overlays.length) {
      const visible = idx.map(i => data.bars[i])
      const add = (points: { t: string; v: number }[], color: string, width: 1 | 2 = 1) => {
        const line = priceChart.addSeries(LineSeries, {
          color, lineWidth: width, priceLineVisible: false, lastValueVisible: false,
          crosshairMarkerVisible: false,
        })
        line.setData(points.map(p => ({ time: t(p.t), value: p.v })))
      }
      const colorOf = (id: OverlayId) => OVERLAYS.find(o => o.id === id)!.color
      /** A band draws its rails solid-ish and its centre faint, so the channel
       * reads as one object rather than three unrelated lines. */
      const band = (c: string, parts: { upper: Point[]; middle?: Point[]; lower: Point[] }) => {
        add(parts.upper, fade(c, 0.75))
        if (parts.middle) add(parts.middle, fade(c, 0.45))
        add(parts.lower, fade(c, 0.75))
      }
      const SIMPLE: Partial<Record<OverlayId, () => Point[]>> = {
        sma20: () => sma(visible, 20),
        sma50: () => sma(visible, 50),
        ema20: () => ema(visible, 20),
        ema50: () => ema(visible, 50),
        wma20: () => wma(visible, 20),
        dema20: () => dema(visible, 20),
        tema20: () => tema(visible, 20),
        vwap: () => vwap(visible),
      }
      for (const id of overlays) {
        const simple = SIMPLE[id]
        if (simple) { add(simple(), colorOf(id)); continue }
        if (id === "bb") band(colorOf(id), bollinger(visible, 20, 2))
        else if (id === "keltner") band(colorOf(id), keltner(visible, 20, 2))
        else if (id === "envelopes") band(colorOf(id), envelopes(visible, 20, 2.5))
        else if (id === "channel") band(colorOf(id), priceChannel(visible, 20))
      }
    }

    // Volume in its OWN pane, not overlaid on the price. Overlaying puts two
    // unrelated scales in one box: a tall volume bar and a low price sit at the
    // same height and read as related when they are not. Theirs is a separate
    // band under the price and this now matches.
    const volumePane = priceChart.addPane()
    const volume = volumePane.addSeries(HistogramSeries, {
      priceFormat: { type: "volume" },
      lastValueVisible: false,
      priceLineVisible: false,
    })
    // A quarter of the price pane's height, which is about what theirs gives it.
    volumePane.setHeight(Math.max(60, Math.round(height * 0.26)))
    volume.setData(idx.map(i => {
      const b = data.bars[i]
      return {
        time: t(b.t),
        value: b.v ?? 0,
        // Tinted by the bar's own direction, so the volume bar under a red
        // candle is red — volume alone says nothing about which way it went.
        // 0.5 alpha, measured on theirs — volume is context under the price,
        // not a second series competing with it.
        color: fade(b.c >= b.o ? gain : loss, 0.55),
      }
    }))

    // Fundamentals get their own pane under the volume band.
    const chosen = metrics.filter(m => fundamentals?.series?.[m]?.length)
    if (chosen.length && fundamentals) {
      const pane = priceChart.addPane()
      pane.setHeight(Math.max(70, Math.round(height * 0.3)))
      const PALETTE = ["#4c8dff", "#34d98c", "#f5a524", "#9353d3", "#f31260"]
      chosen.forEach((id, i) => {
        const colour = PALETTE[i % PALETTE.length]
        const pts = fundamentals.series[id]
          .map(pt => ({ time: (Date.parse(`${pt.t}T00:00:00Z`) / 1000) as UTCTimestamp, value: pt.v }))
          .sort((a, b) => Number(a.time) - Number(b.time))
        if (metricStyle === "bars") {
          const h = pane.addSeries(HistogramSeries, { color: fade(colour, 0.7), priceFormat: { type: "volume" } })
          h.setData(pts)
        } else if (metricStyle === "area") {
          const a = pane.addSeries(AreaSeries, {
            lineColor: colour, topColor: fade(colour, 0.3), bottomColor: fade(colour, 0.02), lineWidth: 2,
          })
          a.setData(pts)
        } else {
          const l = pane.addSeries(LineSeries, { color: colour, lineWidth: 2 })
          l.setData(pts)
        }
      })
    }

    const rsiChart = compact ? null : createChart(rsiRef.current!, { ...base, height: 120 })
    const macdChart = compact ? null : createChart(macdRef.current!, { ...base, height: 120 })
    const charts: IChartApi[] = [priceChart, rsiChart, macdChart].filter(Boolean) as IChartApi[]

    if (!compact && showIndicatorPanes && rsiChart && macdChart && data.indicators_reliable) {
      const rsi = rsiChart.addSeries(LineSeries, { color: RSI_COLOR, lineWidth: 2 })
      rsi.setData(idx.filter(i => data.rsi_9[i] != null)
        .map(i => ({ time: t(data.bars[i].t), value: data.rsi_9[i] as number })))
      for (const lvl of [data.thresholds.rsi_oversold, data.thresholds.rsi_overbought]) {
        rsi.createPriceLine({
          price: lvl, color: text,
          lineWidth: 1, lineStyle: 2, axisLabelVisible: true, title: String(lvl),
        })
      }

      const hist = macdChart.addSeries(HistogramSeries, { color: text })
      hist.setData(idx.filter(i => data.macd_hist[i] != null).map(i => ({
        time: t(data.bars[i].t),
        value: data.macd_hist[i] as number,
        color: (data.macd_hist[i] as number) >= 0 ? gain : loss,
      })))
      const macdLine = macdChart.addSeries(LineSeries, { color: MACD_LINE_COLOR, lineWidth: 2 })
      macdLine.setData(idx.filter(i => data.macd[i] != null)
        .map(i => ({ time: t(data.bars[i].t), value: data.macd[i] as number })))
      const sigLine = macdChart.addSeries(LineSeries, { color: SIGNAL_COLOR, lineWidth: 2 })
      sigLine.setData(idx.filter(i => data.macd_signal[i] != null)
        .map(i => ({ time: t(data.bars[i].t), value: data.macd_signal[i] as number })))
    }

    // Keep the three panes on one shared time axis.
    let syncing = false
    const subs = charts.map(src => {
      const handler = (range: unknown) => {
        if (syncing || !range) return
        syncing = true
        for (const dst of charts) {
          if (dst !== src) dst.timeScale().setVisibleLogicalRange(range as { from: number; to: number })
        }
        syncing = false
      }
      src.timeScale().subscribeVisibleLogicalRangeChange(handler)
      return { src, handler }
    })

    const crosshairs = charts.map(src => {
      const handler = (param: { time?: Time }) => {
        for (const dst of charts) {
          if (dst === src) continue
          if (param.time) dst.setCrosshairPosition(NaN, param.time, dst.panes()[0].getSeries()[0])
          else dst.clearCrosshairPosition()
        }
      }
      src.subscribeCrosshairMove(handler)
      return { src, handler }
    })

    // Feed the OHLCV strip from the price pane's crosshair. Keyed by second,
    // matching the timestamps handed to setData above.
    const byTime = new Map<number, ChartBar>()
    idx.forEach(i => byTime.set(Date.parse(data.bars[i].t) / 1000, data.bars[i]))
    const readoutHandler = (param: { time?: Time; point?: { x: number; y: number } }) => {
      setHovered(param.time ? byTime.get(param.time as number) ?? null : null)
      setAt(param.time && param.point ? param.point : null)
    }
    priceChart.subscribeCrosshairMove(readoutHandler)

    priceChart.timeScale().fitContent()
    if (priceSeries) setApi({ chart: priceChart, series: priceSeries })

    return () => {
      priceChart.unsubscribeCrosshairMove(readoutHandler)
      subs.forEach(s => s.src.timeScale().unsubscribeVisibleLogicalRangeChange(s.handler))
      crosshairs.forEach(c => c.src.unsubscribeCrosshairMove(c.handler))
      setApi(null)
      charts.forEach(c => c.remove())
    }
  }, [data, dark, compact, height, series, overlays, scale, showIndicatorPanes,
      fundamentals, metrics, metricStyle])

  if (compact) {
    return (
      <div className="space-y-1">
        <OhlcvStrip bar={readout} live={hovered == null} />
        {/* Explicit height: the chart is configured autoSize, which measures
            the container — and a bare div has none to measure. */}
        <div className="relative w-full">
          <div ref={priceRef} className="w-full" style={{ height }} />
          {hovered && at && (
            <CrosshairCard
              bar={hovered}
              first={data.bars[0] ?? null}
              symbol={data.symbol}
              at={at}
              width={priceRef.current?.clientWidth ?? 0}
            />
          )}
          {onDrawingsChange && (
            <ChartDrawings
              chart={api?.chart ?? null}
              series={api?.series ?? null}
              tool={tool}
              onToolDone={onToolDone ?? (() => {})}
              drawings={drawings ?? []}
              onChange={onDrawingsChange}
              visible={drawingsVisible}
              height={height}
            />
          )}
        </div>
      </div>
    )
  }

  return (
    <div className="space-y-1">
      <OhlcvStrip bar={readout} live={hovered == null} />
      <div className="relative w-full">
        <div ref={priceRef} className="w-full" style={{ height }} />
        {hovered && at && (
          <CrosshairCard
            bar={hovered}
            first={data.bars[0] ?? null}
            symbol={data.symbol}
            at={at}
            width={priceRef.current?.clientWidth ?? 0}
          />
        )}
        {onDrawingsChange && (
          <ChartDrawings
            chart={api?.chart ?? null}
            series={api?.series ?? null}
            tool={tool}
            onToolDone={onToolDone ?? (() => {})}
            drawings={drawings ?? []}
            onChange={onDrawingsChange}
            visible={drawingsVisible}
            height={height}
          />
        )}
      </div>
      <div className="px-1 text-[14px] font-medium text-muted-foreground">
        RSI-9 {data.indicators_reliable ? "" : "— suppressed, data too sparse"}
      </div>
      <div ref={rsiRef} className="w-full" />
      <div className="px-1 text-[14px] font-medium text-muted-foreground">
        MACD(12,26,9) {data.indicators_reliable ? "" : "— suppressed, data too sparse"}
      </div>
      <div ref={macdRef} className="w-full" />
    </div>
  )
}

/** The O/H/L/C/V line every terminal puts above its chart. Follows the
 * crosshair; falls back to the latest bar so it always says something. */
function OhlcvStrip({ bar, live }: { bar: ChartBar | null; live: boolean }) {
  const compact = useMemo(
    () => new Intl.NumberFormat("en-US", { notation: "compact", maximumFractionDigits: 1 }),
    [],
  )
  if (!bar) return null
  const up = bar.c >= bar.o
  const stamp = new Date(bar.t).toLocaleString("en-US", {
    timeZone: "America/New_York", month: "short", day: "numeric",
    hour: "numeric", minute: "2-digit",
  })
  const Cell = ({ k, v }: { k: string; v: string }) => (
    <span className="whitespace-nowrap">
      <span className="text-muted-foreground">{k}</span>{" "}
      <span className={`num ${up ? "text-gain" : "text-loss"}`}>{v}</span>
    </span>
  )
  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-0.5 px-1 text-[14px]">
      <Cell k="O" v={bar.o.toFixed(2)} />
      <Cell k="H" v={bar.h.toFixed(2)} />
      <Cell k="L" v={bar.l.toFixed(2)} />
      <Cell k="C" v={bar.c.toFixed(2)} />
      <Cell k="V" v={bar.v == null ? "—" : compact.format(bar.v)} />
      <span className="num text-[12px] text-muted-foreground">
        {stamp}{live ? " · latest" : ""}
      </span>
    </div>
  )
}
