import { useTape } from "@/lib/queries"
import type { TapeEntry } from "@/lib/api"
import { Flash } from "@/components/terminal"

/** The market strip pinned under the header — indices, rates, commodities,
 * crypto. Glanceable context, not a quote feed: it refreshes on the minute.
 *
 * IT SCROLLS, the way theirs does. Measured off their board: a `marquee-scroll`
 * keyframe, 50s, linear, infinite, over a `w-max` track holding TWO identical
 * copies of the list. Two copies is the whole trick — translating the track by
 * exactly -50% lands the second copy precisely where the first began, so the
 * loop has no seam and needs no measurement of its own width.
 *
 * It also earns its motion differently from everything else here. The house
 * rule is that motion must encode a change in the data, and a marquee does not
 * — but this is not a data grid. It is a strip too long for its container, and
 * the scroll is what makes the far end reachable at all. The alternative it
 * replaces was a horizontal scrollbar nobody drags.
 *
 * Prices still flash on change, which is the part that does encode data.
 */
function Item({ t }: { t: TapeEntry }) {
  const up = t.change_pct >= 0
  return (
    <div className="flex shrink-0 items-baseline gap-2 px-3 leading-[32px]" title={t.symbol}>
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
}

export function TickerTape() {
  const { data } = useTape()
  const tape = data?.tape ?? []
  if (!tape.length) return null
  return (
    <div className="flex h-[32px] shrink-0 items-stretch border-b border-border bg-background">
      {/* Pinned, outside the scroll — it names what the strip is, so it must
          not wander off the left edge. */}
      <div className="flex shrink-0 items-center gap-1.5 border-r border-border px-3 text-[14px] font-semibold uppercase tracking-[0.06em] text-muted-foreground">
        US Markets
      </div>
      <div className="marquee-viewport min-w-0 flex-1 overflow-hidden">
        <div className="marquee-track flex w-max">
          {tape.map(t => <Item key={t.symbol} t={t} />)}
          {/* The second copy is decoration for the loop, not content — hidden
              from assistive tech so the list is not announced twice. */}
          <div className="flex" aria-hidden="true">
            {tape.map(t => <Item key={`dup-${t.symbol}`} t={t} />)}
          </div>
        </div>
      </div>
    </div>
  )
}
