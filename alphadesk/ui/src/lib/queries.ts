import { useQuery } from "@tanstack/react-query"
import { api, type ChartRange } from "@/lib/api"

/** One query key per endpoint, defined once.
 *
 * Keying by endpoint means every caller shares one in-flight request and one
 * cache entry, however many widgets ask for it — before this, two components
 * polling the same endpoint each ran their own timer and kept their own copy.
 *
 * Intervals are per-endpoint and match how fast the data actually moves —
 * prices in seconds, the earnings calendar in minutes.
 */
export const keys = {
  earnings: ["earnings"] as const,
  screener: ["screener"] as const,
  system: ["system"] as const,
  tape: ["tape"] as const,
  indices: ["indices"] as const,
  crypto: ["crypto"] as const,
  movers: ["movers"] as const,
  quote: (symbol: string) => ["quote", symbol] as const,
  earningsWeek: (start?: string) => ["earnings-week", start ?? "current"] as const,
  chart: (symbol: string, range: ChartRange, interval: string | null) =>
    ["chart", symbol, range, interval ?? "auto"] as const,
}

export const useEarnings = (enabled = true) =>
  useQuery({ queryKey: keys.earnings, queryFn: api.earnings, refetchInterval: 300_000, enabled })

/** One week of the calendar. Keyed by week, so stepping back and forth is
 * instant after the first visit — a calendar week that has already been read
 * does not change while you look at the next one. */
export const useEarningsWeek = (start?: string) =>
  useQuery({
    queryKey: keys.earningsWeek(start),
    queryFn: () => api.earningsWeek(start),
    refetchInterval: 300_000,
  })

export const useScreener = () =>
  useQuery({ queryKey: keys.screener, queryFn: api.screener, refetchInterval: 60_000 })

export const useTape = () =>
  useQuery({ queryKey: keys.tape, queryFn: api.tape, refetchInterval: 60_000 })

export const useMovers = () =>
  useQuery({ queryKey: keys.movers, queryFn: () => api.movers(20), refetchInterval: 120_000 })

export const useIndices = () =>
  useQuery({ queryKey: keys.indices, queryFn: api.indices, refetchInterval: 60_000 })

export const useCrypto = () =>
  useQuery({ queryKey: keys.crypto, queryFn: () => api.crypto(20), refetchInterval: 120_000 })

/** Quote for one symbol. Disabled when there is no symbol, so a widget can
 * mount before the board has been scoped without firing a bad request. */
export const useQuote = (symbol: string) =>
  useQuery({
    queryKey: keys.quote(symbol),
    queryFn: () => api.quote(symbol),
    enabled: !!symbol,
    refetchInterval: 60_000,
  })

export const useSystem = () =>
  useQuery({ queryKey: keys.system, queryFn: api.system, refetchInterval: 30_000 })

/** The price series, polled like everything else on the board.
 *
 * This was the one panel that fetched once and then sat there: a terminal on a
 * second monitor showing a chart frozen at the moment it was opened. Every
 * other endpoint here already refreshes on the cadence its server cache
 * refreshes at, and this now does too.
 *
 * 30s for the intraday ranges, matching _CHART_TTL_S — asking faster returns
 * the same cached bytes. Daily ranges still move (today's bar is live) but not
 * on that timescale, so they poll at five minutes rather than spending a
 * request a minute to redraw an identical year.
 *
 * The previous series is kept across a range or interval change so the canvas
 * is never torn down mid-swap — but NOT across a symbol change, where holding
 * one company's bars under another company's name is the one version of that
 * which actually misleads.
 */
export const useChartSeries = (symbol: string, range: ChartRange, interval: string | null) =>
  useQuery({
    queryKey: keys.chart(symbol, range, interval),
    queryFn: () => api.chartRange(symbol, range, interval ?? undefined),
    enabled: !!symbol,
    refetchInterval: range === "1D" || range === "5D" ? 30_000 : 300_000,
    placeholderData: (prev, prevQuery) =>
      prevQuery && prevQuery.queryKey[1] === symbol ? prev : undefined,
  })
