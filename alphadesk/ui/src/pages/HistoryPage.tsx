import { History } from "@/components/History"
import type { SymbolTimeline } from "@/lib/api"

export default function HistoryPage({
  symbols,
  loading,
}: {
  symbols: SymbolTimeline[]
  loading: boolean
}) {
  return (
    <div className="space-y-3">
      <div>
        <h1 className="text-lg font-bold tracking-tight">History</h1>
        <p className="text-xs text-muted-foreground">
          Every exited pick, grouped by day and market session. P&L is the realized return.
        </p>
      </div>
      <History symbols={symbols} loading={loading} />
    </div>
  )
}
