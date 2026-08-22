import { useSearchParams } from "react-router-dom"
import { EarningsCalendar } from "@/components/EarningsCalendar"
import { EarningsDetail } from "@/components/EarningsDetail"
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
 * Clicking a row scopes the column. That is a SECOND kind of selection in a
 * component that already had one — the calendar's own `selected` is a DATE,
 * filtering the week down to a day — so the two are kept apart: the date
 * selection stays internal, the company selection goes to ?symbol= where the
 * rest of the app can see it. The ticker in each row still links out to
 * Analysis and stops the click from bubbling, or picking a row and leaving the
 * page would happen on the same click.
 */
export default function EarningsPage() {
  const [params, setParams] = useSearchParams()
  const symbol = normalize(params.get("symbol") || "")
  const pick = (sym: string) => {
    const next = new URLSearchParams(params)
    next.set("symbol", sym)
    setParams(next, { replace: true })
  }
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
        // 158, not 150: the ticker strip grew from 32px to 40px and this is
        // the only height in the app measured from the viewport down, so it is
        // the only one that had to follow.
        scroll="calc(100vh - 158px)"
        bodyClassName="overflow-x-auto"
      >
        <EarningsCalendar picked={symbol || null} onPick={pick} />
      </Widget>
      {/* No symbol means no column — the calendar takes the full width rather
          than sitting next to an empty box asking to be filled.
          
          The column is ONE grid child that stacks its panels, not three
          siblings: three span-4 panels beside a span-8 calendar would wrap onto
          the next row and land underneath it instead of alongside. Inside a
          flex parent each Widget's inline gridColumn is simply inert. */}
      {symbol && (
        <div className="col-span-4 flex min-w-0 flex-col gap-2.5">
          <EarningsDetail symbol={symbol} />
          <SymbolNews symbol={symbol} span={4} scroll={220} />
        </div>
      )}
    </div>
  )
}
