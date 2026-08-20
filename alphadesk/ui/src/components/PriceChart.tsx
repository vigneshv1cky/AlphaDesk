import { useEffect, useMemo, useRef, useState } from "react"
import {
  CandlestickSeries,
  createChart,
  HistogramSeries,
  LineSeries,
  type IChartApi,
  type Time,
  type UTCTimestamp,
} from "lightweight-charts"
import type { ChartBar, ChartSeries } from "@/lib/api"

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

/** Candles + RSI-9 + MACD(12,26,9), in three stacked panes.
 *
 * Indicators are drawn ONLY when the series says they're trustworthy — see
 * `indicators_reliable` in api.ts. A MACD computed on 61 prints spread across
 * five sessions renders exactly like one computed on 1,950 real minute bars,
 * and the whole point of this app is to not fool its own operator. */
export function PriceChart({ data, dark }: { data: ChartSeries; dark: boolean }) {
  const priceRef = useRef<HTMLDivElement>(null)
  const rsiRef = useRef<HTMLDivElement>(null)
  const macdRef = useRef<HTMLDivElement>(null)
  // Which bar the OHLCV strip describes. null = not hovering, and the strip
  // falls back to the most recent bar so it is never blank.
  const [hovered, setHovered] = useState<ChartBar | null>(null)
  const lastBar = data.bars.length ? data.bars[data.bars.length - 1] : null
  const readout = hovered ?? lastBar

  useEffect(() => {
    if (!priceRef.current || !rsiRef.current || !macdRef.current) return

    const text = cssVar("--muted-foreground")
    const grid = cssVar("--border")
    const gain = cssVar("--gain")
    const loss = cssVar("--loss")
    const base = {
      layout: { background: { color: "transparent" }, textColor: text, attributionLogo: false },
      grid: { vertLines: { color: grid }, horzLines: { color: grid } },
      rightPriceScale: { borderColor: grid },
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

    const priceChart = createChart(priceRef.current, { ...base, height: 320 })
    const candles = priceChart.addSeries(CandlestickSeries, {
      upColor: gain, downColor: loss,
      wickUpColor: gain, wickDownColor: loss, borderVisible: false,
    })
    candles.setData(idx.map(i => {
      const b = data.bars[i]
      return { time: t(b.t), open: b.o, high: b.h, low: b.l, close: b.c }
    }))

    // Volume as an overlay in the price pane's bottom fifth, the way every
    // terminal draws it — its own price scale ("" means overlay), so the
    // candles keep the full height of the right axis.
    const volume = priceChart.addSeries(HistogramSeries, {
      priceFormat: { type: "volume" },
      priceScaleId: "",
      lastValueVisible: false,
      priceLineVisible: false,
    })
    volume.priceScale().applyOptions({ scaleMargins: { top: 0.8, bottom: 0 } })
    volume.setData(idx.map(i => {
      const b = data.bars[i]
      return {
        time: t(b.t),
        value: b.v ?? 0,
        // Tinted by the bar's own direction, so the volume bar under a red
        // candle is red — volume alone says nothing about which way it went.
        color: b.c >= b.o ? gain : loss,
      }
    }))

    const rsiChart = createChart(rsiRef.current, { ...base, height: 120 })
    const macdChart = createChart(macdRef.current, { ...base, height: 120 })
    const charts: IChartApi[] = [priceChart, rsiChart, macdChart]

    if (data.indicators_reliable) {
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
    const readoutHandler = (param: { time?: Time }) => {
      setHovered(param.time ? byTime.get(param.time as number) ?? null : null)
    }
    priceChart.subscribeCrosshairMove(readoutHandler)

    priceChart.timeScale().fitContent()

    return () => {
      priceChart.unsubscribeCrosshairMove(readoutHandler)
      subs.forEach(s => s.src.timeScale().unsubscribeVisibleLogicalRangeChange(s.handler))
      crosshairs.forEach(c => c.src.unsubscribeCrosshairMove(c.handler))
      charts.forEach(c => c.remove())
    }
  }, [data, dark])

  return (
    <div className="space-y-1">
      <OhlcvStrip bar={readout} live={hovered == null} />
      <div ref={priceRef} className="w-full" />
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
