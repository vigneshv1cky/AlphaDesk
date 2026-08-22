import { useTape } from "@/lib/queries"
import { Flash } from "@/components/terminal"

/** The market strip pinned under the header — indices, rates, commodities,
 * crypto. Glanceable context, not a quote feed: it refreshes on the minute.
 *
 * It scrolls horizontally rather than wrapping, because a tape that reflows to
 * a second line pushes the whole terminal down every time a number gets wider.
 *
 * Prices flash on change. This strip is the one thing on screen you are never
 * looking directly at — it sits above whatever you are actually reading — so a
 * value that moves here is the likeliest of any to move unseen. That is exactly
 * the case the house rule allows motion for: it encodes a change in the data
 * and nothing else.
 */
export function TickerTape() {
  const { data } = useTape()
  const tape = data?.tape ?? []
  if (!tape.length) return null
  return (
    <div className="flex h-[32px] shrink-0 items-stretch overflow-x-auto border-b border-border bg-background">
      <div className="flex shrink-0 items-center gap-1.5 border-r border-border px-3 text-[14px] font-semibold uppercase tracking-[0.06em] text-muted-foreground">
        US Markets
      </div>
      {tape.map(t => {
        const up = t.change_pct >= 0
        return (
          <div
            key={t.symbol}
            className="flex shrink-0 items-baseline gap-2 px-3 leading-[32px]"
            title={t.symbol}
          >
            <span className="text-[14px] font-semibold uppercase tracking-[0.02em] text-foreground">
              {t.label}
            </span>
            <Flash value={t.price} className="num text-[14px] text-muted-foreground">
              {t.price.toLocaleString(undefined, { maximumFractionDigits: 2 })}
            </Flash>
            <span className={`num text-[14px] ${up ? "text-gain" : "text-loss"}`}>
              {up ? "+" : ""}{t.change_pct.toFixed(2)}%
            </span>
          </div>
        )
      })}
    </div>
  )
}
