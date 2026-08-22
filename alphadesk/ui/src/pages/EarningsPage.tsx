import { useSearchParams } from "react-router-dom"
import { EarningsCalendar } from "@/components/EarningsCalendar"
import { SymbolNews } from "@/components/SymbolNews"
import { Widget } from "@/components/terminal"
import { normalize } from "@/lib/watchlist"

/** The week's reporters, with a column for the company you are looking at.
 *
 * Their earnings view runs the calendar beside a company-scoped column —
 * revenue vs earnings, EPS estimate against actual, analyst estimates, news.
 * The column here follows ?symbol=, which is how every other view in this app
 * is scoped and what the AI rail already reads.
 *
 * It does NOT follow a click in the calendar, because the calendar's own
 * selection is a DATE (it filters the week down to one day), not a company.
 * Wiring row-click to the column means adding a second kind of selection to a
 * component that already has one, and the ticker in each row is currently a
 * link out to Analysis — a useful affordance to keep. Left deliberately, not
 * forgotten.
 */
export default function EarningsPage() {
  const [params] = useSearchParams()
  const symbol = normalize(params.get("symbol") || "")
  return (
    <div className="collage">
      <Widget
        span={symbol ? 8 : 12}
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
      {/* No symbol means no column — the calendar takes the full width rather
          than sitting next to an empty box asking to be filled. */}
      {symbol && <SymbolNews symbol={symbol} span={4} scroll="calc(100vh - 150px)" />}
    </div>
  )
}
