import { useSearchParams } from "react-router-dom"
import { SymbolSearch } from "@/components/SymbolSearch"

/** The bar above the canvas: which view you are on, which symbol it is scoped
 * to, and the view-level action. AlphaSpace puts the symbol here as a
 * removable chip rather than inside each widget, so one change re-scopes the
 * whole board — the chip writes ?symbol=, which every widget already reads. */
export function ViewHeader({ title }: { title: string }) {
  const [params, setParams] = useSearchParams()
  const symbol = (params.get("symbol") || "").toUpperCase()
  const clear = () => {
    const next = new URLSearchParams(params)
    next.delete("symbol")
    setParams(next, { replace: true })
  }
  return (
    <div className="flex h-[42px] shrink-0 items-center gap-2 px-3">
      <h1 className="text-[15px] font-semibold">{title}</h1>
      {symbol && (
        <span className="inline-flex items-center gap-1.5 rounded-full border border-border bg-muted px-2 py-[3px] text-[14px]">
          <span className="h-1.5 w-1.5 rounded-full bg-accent" />
          {symbol}
          <button onClick={clear} aria-label={`Clear ${symbol}`}
            className="text-muted-foreground hover:text-foreground">✕</button>
        </span>
      )}
      <SymbolSearch />
      <div className="flex-1" />
    </div>
  )
}
