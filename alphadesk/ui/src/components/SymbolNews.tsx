import { useScreener } from "@/lib/queries"
import { Empty, Widget } from "@/components/terminal"

/** Headlines for ONE company, from the window the screener already holds.
 *
 * No new endpoint and no new request: /api/screener is a shared query key that
 * the markets board is already polling, so scoping it to a symbol here costs
 * nothing. If the symbol is not in the window there is nothing to show, and it
 * says which symbol it found nothing for rather than rendering an empty box.
 */
export function SymbolNews({ symbol, span = 12, scroll = 300 }: {
  symbol: string
  span?: number
  scroll?: number | string
}) {
  const { data, isPending } = useScreener()
  const row = (data?.symbols ?? []).find(r => r.symbol === symbol)
  const headlines = row?.headlines ?? []

  return (
    <Widget
      span={span}
      symbol={symbol}
      title="News"
      subtitle={headlines.length ? `${headlines.length} in the window` : undefined}
      scroll={scroll}
    >
      {isPending ? <Empty>loading…</Empty>
       : !headlines.length ? <Empty>no headlines for {symbol} in the current window</Empty> : (
        <div>
          {headlines.map(h => (
            <a
              key={h.url}
              href={h.url}
              target="_blank"
              rel="noreferrer"
              className="row-rule block px-3 py-[8px] hover:bg-muted/60"
            >
              <span className="text-[14px] leading-snug">{h.title}</span>
              <span className="ml-2 whitespace-nowrap text-[12px] text-muted-foreground">
                — {h.source}
              </span>
            </a>
          ))}
        </div>
      )}
    </Widget>
  )
}
