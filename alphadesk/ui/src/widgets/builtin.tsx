import { useScreener } from "@/lib/queries"
import { Empty, Widget } from "@/components/terminal"
import { registerWidget } from "@/widgets/registry"
import { TILE_BODY_HEIGHT } from "@/widgets/tile"

/** The tiles AlphaDesk ships with.
 *
 * Each is an ordinary component that fetches its own data and registers
 * itself at import time. A plugin adds a tile the same way; re-registering a
 * built-in id replaces it. Ordering leaves gaps so third-party tiles can slot
 * between these without renumbering.
 */

function NewsTape() {
  const screener = useScreener()
  const withNews = (screener.data?.symbols ?? []).filter(s => s.article_count > 0)
  const headlines = withNews
    .flatMap(s => s.headlines.map(h => ({ ...h, symbol: s.symbol })))
    .sort((a, b) => String(b.published_at ?? "").localeCompare(String(a.published_at ?? "")))
    .slice(0, 60)
  return (
  <Widget span={12} title="Market News" subtitle={`${headlines.length} headlines, newest first`} scroll={TILE_BODY_HEIGHT}>
    {!screener.data ? (
      <Empty>loading…</Empty>
    ) : headlines.length === 0 ? (
      <Empty>no news in the window</Empty>
    ) : (
      <ul>
        {headlines.map((h, i) => (
          <li key={i} className="border-b border-grid-line last:border-b-0 hover:bg-muted/60">
            <a href={h.url} target="_blank" rel="noreferrer" className="block px-2 py-1">
              <span className="num mr-1.5 text-[12px] font-semibold text-accent">{h.symbol}</span>
              <span className="text-[14px]">{h.title}</span>
              <span className="ml-1.5 text-[12px] text-muted-foreground">— {h.source}</span>
            </a>
          </li>
        ))}
      </ul>
    )}
  </Widget>
  )
}

// Their Markets board is six panels: chart, equity overview, three movers and
// market news. The status strip, the window list and reporting-soon are not
// among them, and both of the latter now own a whole view (/news, /earnings),
// so they were duplicates that made the board ragged for no new information.
// Deleted rather than left unregistered — an unused component is a component
// nobody maintains. Git has them if a deployment wants the tiles back.
registerWidget({ id: "news-tape", order: 30, component: NewsTape })
