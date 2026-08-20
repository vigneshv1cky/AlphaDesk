import { EarningsCalendar } from "@/components/EarningsCalendar"
import { Widget } from "@/components/terminal"

export default function EarningsPage() {
  return (
    <div className="collage">
      <Widget
        span={12}
        title="Earnings calendar"
        subtitle="a week at a time — every reporter, biggest first within each day"
        // The panel scrolls, not the page — which is what lets the column
        // header stay put. Widget sets overflow-hidden for its rounded corner,
        // and that makes the widget the containing block for anything sticky
        // inside it, so a sticky header only holds when this body is the
        // scroller. Viewport-relative rather than a fixed height: a calendar
        // is as tall as the screen allows.
        scroll="calc(100vh - 150px)"
        bodyClassName="overflow-x-auto"
      >
        <EarningsCalendar />
      </Widget>
    </div>
  )
}
