import { LivePositions } from "@/components/LivePositions"
import type { LivePick } from "@/lib/api"

export default function LivePage({
  rows,
  market,
  loading,
}: {
  rows: LivePick[]
  market: string
  loading: boolean
}) {
  return (
    <div className="space-y-3">
      <div>
        <h1 className="text-lg font-bold tracking-tight">Live Positions</h1>
        <p className="text-xs text-muted-foreground">
          Open picks tracked against the current price — equal $10 fractional sizing,
          session-scoped (each trade exits at its session close).
        </p>
      </div>
      <LivePositions rows={rows} market={market} loading={loading} />
    </div>
  )
}
