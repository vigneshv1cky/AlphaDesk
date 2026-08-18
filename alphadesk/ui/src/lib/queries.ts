import { useQuery } from "@tanstack/react-query"
import { api } from "@/lib/api"

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
  movers: ["movers"] as const,
  quote: (symbol: string) => ["quote", symbol] as const,
}

export const useEarnings = (enabled = true) =>
  useQuery({ queryKey: keys.earnings, queryFn: api.earnings, refetchInterval: 300_000, enabled })

export const useScreener = () =>
  useQuery({ queryKey: keys.screener, queryFn: api.screener, refetchInterval: 60_000 })

export const useTape = () =>
  useQuery({ queryKey: keys.tape, queryFn: api.tape, refetchInterval: 60_000 })

export const useMovers = () =>
  useQuery({ queryKey: keys.movers, queryFn: () => api.movers(20), refetchInterval: 120_000 })

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

