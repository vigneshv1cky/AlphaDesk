import { useScreener } from "@/lib/queries"
import { Empty, Widget } from "@/components/terminal"
import { WindowTable } from "@/components/WindowTable"
import { NewsTape } from "@/widgets/builtin"

/** The front door — an unranked inventory of what's in the window.
 *
 * Nothing here is ranked. The list is every symbol with fresh news or a
 * report inside the horizon, alphabetical: ordering a list is itself a
 * judgment, and the judgment is the operator's. Sorting a column is you
 * choosing how to read it, which is a different thing.
 *
 * The AI is NOT on this page. Asking moved to the rail (components/AiRail),
 * which reaches the same window-wide call from any route — so the question box
 * no longer has to live on the page whose data it happens to read. */
export default function NewsPage() {
  const { data, error } = useScreener()
  const rows = data?.symbols ?? null
  const err = error ? String(error.message ?? error) : null

  const withNews = rows?.filter(r => r.article_count > 0).length ?? 0
  const reporting = rows?.filter(r => r.report_date).length ?? 0

  return (
    <div className="collage">
      <Widget
        // Two columns, the way their news view runs a main list beside a
        // secondary one. The window keeps the width because it is the thing
        // this page is for; the headline tape is the sidebar to it.
        //
        // Their right column also carries a video panel. We have no video
        // source, and a placeholder for one would be a promise the page cannot
        // keep, so the tape simply takes the whole column.
        span={8}
        title="News window"
        subtitle={
          rows === null
            ? "loading…"
            : `${rows.length} symbols · ${withNews} with news · ${reporting} reporting soon — alphabetical, nothing ranked · ask in the AI panel →`
        }
      >
        {err && <Empty>{err}</Empty>}
        {rows === null && !err && <Empty>loading…</Empty>}
        {rows !== null && rows.length === 0 && (
          <Empty>nothing in the window — no upcoming earnings and no fresh news</Empty>
        )}
        {rows !== null && rows.length > 0 && <WindowTable rows={rows} />}
      </Widget>
      <NewsTape span={4} />
    </div>
  )
}



