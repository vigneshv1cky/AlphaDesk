import { EarningsCalendar } from "@/components/EarningsCalendar"
import { Widget } from "@/components/terminal"

export default function EarningsPage() {
  return (
    <div className="collage">
      <Widget
        span={12}
        title="Earnings calendar"
        subtitle="a week at a time — every reporter, biggest first within each day"
      >
        <EarningsCalendar />
      </Widget>
    </div>
  )
}
