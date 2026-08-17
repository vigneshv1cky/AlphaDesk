import { Earnings } from "@/components/Earnings"
import type { EarningsRow } from "@/lib/api"

export default function EarningsPage({
  earnings,
}: {
  earnings: { upcoming: EarningsRow[]; reported: EarningsRow[] } | null
}) {
  return (
    <div className="space-y-3">
      <div>
        <h1 className="text-lg font-bold tracking-tight">Earnings Calendar</h1>
        <p className="text-xs text-muted-foreground">
          Who reports next and who just reported — the candidate source the Screener ranks against.
        </p>
      </div>
      <Earnings earnings={earnings} />
    </div>
  )
}
