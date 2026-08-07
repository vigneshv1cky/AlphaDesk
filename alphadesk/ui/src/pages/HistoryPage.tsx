import { History } from "@/components/History"
import type { Stats, SymbolTimeline } from "@/lib/api"

export default function HistoryPage({
  symbols,
  stats,
  loading,
}: {
  symbols: SymbolTimeline[]
  stats: Stats | null
  loading: boolean
}) {
  return (
    <div className="space-y-3">
      <div>
        <h1 className="text-lg font-bold tracking-tight">History</h1>
        <p className="text-xs text-muted-foreground">
          Every exited pick, grouped by day. P&L is the realized return; Alpha is the
          return vs SPY after friction.
        </p>
      </div>
      <History symbols={symbols} stats={stats} loading={loading} />
    </div>
  )
}
