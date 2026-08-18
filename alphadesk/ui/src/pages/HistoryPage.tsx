import { History } from "@/components/History"
import type { SymbolTimeline } from "@/lib/api"
import { Widget } from "@/components/terminal"

export default function HistoryPage({
  symbols,
  loading,
}: {
  symbols: SymbolTimeline[]
  loading: boolean
}) {
  return (
    <div className="collage">
      <Widget
        span={12}
        title="History"
        subtitle="every exited pick, grouped by day and session · P&L is the realized return"
      >
        <History symbols={symbols} loading={loading} />
      </Widget>
    </div>
  )
}
