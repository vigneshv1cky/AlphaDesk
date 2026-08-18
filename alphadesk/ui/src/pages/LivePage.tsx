import { LivePositions } from "@/components/LivePositions"
import type { LivePick } from "@/lib/api"
import { Widget } from "@/components/terminal"

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
    <div className="collage">
      <Widget
        span={12}
        title="Live positions"
        subtitle="booked by you · automated target/stop/trailing/session-close · no autonomous entries"
      >
        <LivePositions rows={rows} market={market} loading={loading} />
      </Widget>
    </div>
  )
}
