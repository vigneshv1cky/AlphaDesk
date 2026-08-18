import { useTape } from "@/lib/queries"

/** The market strip pinned under the header — indices, rates, commodities,
 * crypto. Glanceable context, not a quote feed: it refreshes on the minute.
 *
 * It scrolls horizontally rather than wrapping, because a tape that reflows to
 * a second line pushes the whole terminal down every time a number gets wider.
 */
export function TickerTape() {
  const { data } = useTape()
  const tape = data?.tape ?? []
  if (!tape.length) return null
  return (
    <div className="flex h-[26px] shrink-0 items-stretch overflow-x-auto border-b border-border bg-panel">
      {tape.map(t => {
        const up = t.change_pct >= 0
        return (
          <div
            key={t.symbol}
            className="flex shrink-0 items-baseline gap-1.5 border-r border-grid-line px-3 leading-[26px]"
            title={t.symbol}
          >
            <span className="text-[10px] font-semibold uppercase tracking-[0.04em] text-muted-foreground">
              {t.label}
            </span>
            <span className="num text-[11px] font-semibold">
              {t.price.toLocaleString(undefined, { maximumFractionDigits: 2 })}
            </span>
            <span className={`num text-[10px] ${up ? "text-gain" : "text-loss"}`}>
              {up ? "+" : ""}{t.change_pct.toFixed(2)}%
            </span>
          </div>
        )
      })}
    </div>
  )
}
