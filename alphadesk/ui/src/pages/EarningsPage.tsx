import { Earnings } from "@/components/Earnings"
import type { EarningsRow } from "@/lib/api"
import { Widget } from "@/components/terminal"

export default function EarningsPage({
  earnings,
}: {
  earnings: { upcoming: EarningsRow[]; reported: EarningsRow[] } | null
}) {
  return (
    <div className="collage">
      <Widget
        span={12}
        title="Earnings calendar"
        subtitle="who reports next and who just reported — the symbols the Screener window is drawn from"
      >
        <Earnings earnings={earnings} />
      </Widget>
    </div>
  )
}
