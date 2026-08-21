import { SymbolSearch } from "@/components/SymbolSearch"
import { useBoardSymbols } from "@/lib/boardSymbols"

/** The bar above the canvas: which view you are on, the symbols you have open,
 * and the view-level action.
 *
 * The chips are a TAB STRIP, not a layout. Only the active one scopes the
 * board — chart, equity overview and the AI rail all follow it — so a second
 * chip is a second thing you can switch to rather than a second chart drawn
 * beside the first. Keeping the symbol here instead of inside each widget is
 * what lets one click re-scope the whole board at once.
 *
 * The strip is allowed to be empty. Closing the last chip is a real state, not
 * an edge case to prevent: the tiles fall back to their own "pick a symbol"
 * placeholders, and the `+` is the way back.
 */
export function ViewHeader({ title }: { title: string }) {
  const { symbols, active, activate, remove } = useBoardSymbols()
  return (
    <div className="flex h-[42px] shrink-0 items-center gap-2 px-3">
      <h1 className="text-[15px] font-semibold">{title}</h1>
      {symbols.map(symbol => {
        const on = symbol === active
        return (
          <span
            key={symbol}
            className={`inline-flex items-center gap-1.5 rounded-full border px-2 py-[3px] text-[14px] transition-colors ${
              on
                ? "border-border bg-muted text-foreground"
                : "border-transparent text-muted-foreground hover:bg-muted/60 hover:text-foreground"
            }`}
          >
            {/* The dot is what says "this is the one on screen". Inactive chips
                keep it, dimmed, so the row of chips stays aligned instead of
                reflowing by 12px as you switch between them. */}
            <span className={`h-1.5 w-1.5 rounded-full ${on ? "bg-accent" : "bg-muted-foreground/40"}`} />
            <button
              type="button"
              onClick={() => activate(symbol)}
              aria-current={on ? "true" : undefined}
              aria-label={on ? `${symbol}, showing` : `Show ${symbol}`}
              // Not disabled when active: a disabled control in a row of
              // identical ones reads as broken rather than as already-selected.
              className="cursor-pointer"
            >
              {symbol}
            </button>
            <button
              type="button"
              onClick={() => remove(symbol)}
              aria-label={`Close ${symbol}`}
              className="text-muted-foreground hover:text-foreground"
            >
              ✕
            </button>
          </span>
        )
      })}
      <SymbolSearch />
      <div className="flex-1" />
    </div>
  )
}
