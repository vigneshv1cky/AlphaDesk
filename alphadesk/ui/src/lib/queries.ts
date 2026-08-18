import { useQuery } from "@tanstack/react-query"
import { api } from "@/lib/api"

/** One query key per endpoint, defined once.
 *
 * This is the whole point of moving to TanStack Query: before, App.tsx and
 * DashboardPage each ran their own `setInterval` against /api/live and
 * /api/earnings, so those endpoints were fetched twice on every tick and the
 * two copies could disagree. Keying by endpoint means every caller shares one
 * in-flight request and one cache entry, however many widgets ask for it.
 *
 * Intervals are per-endpoint and match how fast the data actually moves —
 * prices in seconds, the earnings calendar in minutes.
 */
export const keys = {
  live: ["live"] as const,
  timelines: ["timelines"] as const,
  earnings: ["earnings"] as const,
  screener: ["screener"] as const,
  system: ["system"] as const,
  stats: ["stats"] as const,
  performance: (days: number) => ["performance", days] as const,
}

export const useLive = () =>
  useQuery({ queryKey: keys.live, queryFn: api.live, refetchInterval: 15_000 })

export const useTimelines = () =>
  useQuery({ queryKey: keys.timelines, queryFn: api.timelines, refetchInterval: 60_000 })

export const useEarnings = (enabled = true) =>
  useQuery({ queryKey: keys.earnings, queryFn: api.earnings, refetchInterval: 300_000, enabled })

export const useScreener = () =>
  useQuery({ queryKey: keys.screener, queryFn: api.screener, refetchInterval: 60_000 })

export const useSystem = () =>
  useQuery({ queryKey: keys.system, queryFn: api.system, refetchInterval: 30_000 })

export const useStats = () =>
  useQuery({ queryKey: keys.stats, queryFn: api.stats, refetchInterval: 60_000 })

export const usePerformance = (days = 30) =>
  useQuery({ queryKey: keys.performance(days), queryFn: () => api.performance(days), refetchInterval: 60_000 })
