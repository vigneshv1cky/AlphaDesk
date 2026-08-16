import { useEffect, useRef } from "react"
import {
  CandlestickSeries,
  createChart,
  HistogramSeries,
  LineSeries,
  type IChartApi,
  type Time,
  type UTCTimestamp,
} from "lightweight-charts"
import type { ChartSeries } from "@/lib/api"

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

  useEffect(() => {
    if (!priceRef.current || !rsiRef.current || !macdRef.current) return

    const text = dark ? "#a1a1aa" : "#52525b"
    const grid = dark ? "#27272a" : "#e4e4e7"
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
      upColor: "#16a34a", downColor: "#dc2626",
      wickUpColor: "#16a34a", wickDownColor: "#dc2626", borderVisible: false,
    })
    candles.setData(idx.map(i => {
      const b = data.bars[i]
      return { time: t(b.t), open: b.o, high: b.h, low: b.l, close: b.c }
    }))

    const rsiChart = createChart(rsiRef.current, { ...base, height: 120 })
    const macdChart = createChart(macdRef.current, { ...base, height: 120 })
    const charts: IChartApi[] = [priceChart, rsiChart, macdChart]

    if (data.indicators_reliable) {
      const rsi = rsiChart.addSeries(LineSeries, { color: "#7c3aed", lineWidth: 2 })
      rsi.setData(idx.filter(i => data.rsi_9[i] != null)
        .map(i => ({ time: t(data.bars[i].t), value: data.rsi_9[i] as number })))
      for (const lvl of [data.thresholds.rsi_oversold, data.thresholds.rsi_overbought]) {
        rsi.createPriceLine({
          price: lvl, color: dark ? "#52525b" : "#a1a1aa",
          lineWidth: 1, lineStyle: 2, axisLabelVisible: true, title: String(lvl),
        })
      }

      const hist = macdChart.addSeries(HistogramSeries, { color: "#71717a" })
      hist.setData(idx.filter(i => data.macd_hist[i] != null).map(i => ({
        time: t(data.bars[i].t),
        value: data.macd_hist[i] as number,
        color: (data.macd_hist[i] as number) >= 0 ? "#16a34a" : "#dc2626",
      })))
      const macdLine = macdChart.addSeries(LineSeries, { color: "#2563eb", lineWidth: 2 })
      macdLine.setData(idx.filter(i => data.macd[i] != null)
        .map(i => ({ time: t(data.bars[i].t), value: data.macd[i] as number })))
      const sigLine = macdChart.addSeries(LineSeries, { color: "#f59e0b", lineWidth: 2 })
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

    priceChart.timeScale().fitContent()

    return () => {
      subs.forEach(s => s.src.timeScale().unsubscribeVisibleLogicalRangeChange(s.handler))
      crosshairs.forEach(c => c.src.unsubscribeCrosshairMove(c.handler))
      charts.forEach(c => c.remove())
    }
  }, [data, dark])

  return (
    <div className="space-y-1">
      <div ref={priceRef} className="w-full" />
      <div className="px-1 text-[11px] font-medium text-muted-foreground">
        RSI-9 {data.indicators_reliable ? "" : "— suppressed, data too sparse"}
      </div>
      <div ref={rsiRef} className="w-full" />
      <div className="px-1 text-[11px] font-medium text-muted-foreground">
        MACD(12,26,9) {data.indicators_reliable ? "" : "— suppressed, data too sparse"}
      </div>
      <div ref={macdRef} className="w-full" />
    </div>
  )
}
