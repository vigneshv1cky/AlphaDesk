import { useMemo, useRef, useState } from "react"
import { Link } from "react-router-dom"
import {
  createColumnHelper,
  flexRender,
  getCoreRowModel,
  getFilteredRowModel,
  getSortedRowModel,
  useReactTable,
  type SortingState,
} from "@tanstack/react-table"
import { useVirtualizer } from "@tanstack/react-virtual"
import type { ScreenerRow } from "@/lib/api"
import { Btn, Empty, fieldCls } from "@/components/terminal"

/** The window inventory as a real data grid.
 *
 * IMPORTANT — this sorts and filters, it does NOT rank. Those are different
 * things: a rank is the app asserting which symbols matter (the thing this
 * screener deliberately stopped doing); a sort is you choosing how to read a
 * list you already have. The DEFAULT is still alphabetical, and no ordering is
 * applied unless you click a header.
 *
 * Virtualized because the window runs to a few hundred symbols — measured at
 * 377 live, of which only ~92 had news — and mounting every row plus its
 * headlines makes scrolling gritty for no benefit when ~30 are on screen.
 */
const col = createColumnHelper<ScreenerRow>()

const ROW_H = 24

export function WindowTable({ rows }: { rows: ScreenerRow[] }) {
  const [sorting, setSorting] = useState<SortingState>([])
  const [query, setQuery] = useState("")
  const [newsOnly, setNewsOnly] = useState(false)
  const scrollRef = useRef<HTMLDivElement>(null)

  const data = useMemo(
    () => (newsOnly ? rows.filter(r => r.article_count > 0) : rows),
    [rows, newsOnly],
  )

  const columns = useMemo(() => [
    col.accessor("symbol", {
      header: "Symbol",
      cell: c => <span className="font-semibold">{c.getValue()}</span>,
      size: 90,
    }),
    col.accessor("article_count", {
      header: "News",
      cell: c => (
        <span className={c.getValue() ? "num" : "num text-muted-foreground/40"}>
          {c.getValue() || "—"}
        </span>
      ),
      size: 60,
    }),
    col.accessor("report_date", {
      header: "Reports",
      cell: c => <span className="tnum text-muted-foreground">{c.getValue() ?? "—"}</span>,
      // Nulls last regardless of direction: "no report date" is absence of a
      // value, not an early or late one, so it should never win the top slot.
      sortUndefined: "last",
      size: 100,
    }),
    col.display({
      id: "headline",
      header: "Latest",
      cell: c => {
        const h = c.row.original.headlines[0]
        if (!h) return <span className="text-muted-foreground/40">—</span>
        return (
          <a
            href={h.url}
            target="_blank"
            rel="noreferrer"
            onClick={e => e.stopPropagation()}
            className="block truncate text-muted-foreground hover:text-foreground hover:underline"
            title={h.title}
          >
            {h.title}
            <span className="ml-1.5 text-[12px] text-muted-foreground/70">— {h.source}</span>
          </a>
        )
      },
    }),
    col.display({
      id: "go",
      header: "",
      cell: c => (
        <div className="flex justify-end gap-1">
          <Link to={`/filings?symbol=${encodeURIComponent(c.row.original.symbol)}`} onClick={e => e.stopPropagation()}>
            <Btn variant="ghost">filings</Btn>
          </Link>
          <Link to={`/analysis?symbol=${encodeURIComponent(c.row.original.symbol)}`} onClick={e => e.stopPropagation()}>
            <Btn>chart →</Btn>
          </Link>
        </div>
      ),
      size: 150,
    }),
  ], [])

  const table = useReactTable({
    data,
    columns,
    state: { sorting, globalFilter: query },
    onSortingChange: setSorting,
    onGlobalFilterChange: setQuery,
    // Match on the symbol only. The default global filter stringifies every
    // column, so typing "AA" would also match any row whose headline happened
    // to contain it — noise when you are looking up a ticker.
    globalFilterFn: (row, _id, value) =>
      String(row.original.symbol).toLowerCase().includes(String(value).toLowerCase()),
    getCoreRowModel: getCoreRowModel(),
    getFilteredRowModel: getFilteredRowModel(),
    getSortedRowModel: getSortedRowModel(),
  })

  const modelRows = table.getRowModel().rows
  const virt = useVirtualizer({
    count: modelRows.length,
    getScrollElement: () => scrollRef.current,
    estimateSize: () => ROW_H,
    overscan: 12,
  })
  const items = virt.getVirtualItems()

  return (
    <div>
      <div className="flex flex-wrap items-center gap-1.5 border-b border-border p-1">
        <input
          value={query}
          onChange={e => setQuery(e.target.value)}
          placeholder="Filter symbol…"
          className={`${fieldCls} w-40 uppercase tracking-[0.04em]`}
        />
        <Btn active={newsOnly} onClick={() => setNewsOnly(v => !v)}>
          with news only
        </Btn>
        {(query || newsOnly || sorting.length > 0) && (
          <Btn
            variant="ghost"
            onClick={() => { setQuery(""); setNewsOnly(false); setSorting([]) }}
          >
            reset
          </Btn>
        )}
        <span className="tnum ml-auto text-[12px] text-muted-foreground">
          {modelRows.length} / {rows.length}
        </span>
      </div>

      {/* The header is flex, NOT a <table>. A table stretches its columns to
          fill the available width, while the virtualized body below is
          absolutely-positioned flex rows at fixed pixel widths — mixing the
          two layout models puts every header over the wrong column. Both use
          the identical width/flex rule so they stay locked together. */}
      {table.getHeaderGroups().map(hg => (
        <div key={hg.id} className="flex w-full border-b border-border bg-panel-header">
          {hg.headers.map(h => {
            const dir = h.column.getIsSorted()
            const size = h.column.columnDef.size
            return (
              <div
                key={h.id}
                style={{ width: size, flex: size ? "0 0 auto" : "1 1 0%" }}
                onClick={h.column.getToggleSortingHandler()}
                className={`flex h-[22px] min-w-0 select-none items-center truncate px-2 text-[11px] font-semibold uppercase tracking-[0.06em] text-muted-foreground ${
                  h.column.getCanSort() ? "cursor-pointer hover:text-foreground" : ""
                }`}
              >
                {flexRender(h.column.columnDef.header, h.getContext())}
                {dir && <span className="ml-1">{dir === "asc" ? "▲" : "▼"}</span>}
              </div>
            )
          })}
        </div>
      ))}

      {modelRows.length === 0 ? (
        <Empty>no symbols match that filter</Empty>
      ) : (
        <div ref={scrollRef} className="max-h-[560px] overflow-y-auto">
          {/* One spacer div sized to the full list, with only the visible
              window absolutely positioned inside it. A <table> can't be
              virtualized this way without breaking row layout, so the body is
              a grid of divs whose columns mirror the header widths. */}
          <div style={{ height: virt.getTotalSize(), position: "relative" }}>
            {items.map(vi => {
              const row = modelRows[vi.index]
              return (
                <div
                  key={row.id}
                  className="absolute left-0 flex w-full items-center border-b border-grid-line hover:bg-muted/60"
                  style={{ height: ROW_H, transform: `translateY(${vi.start}px)` }}
                >
                  {row.getVisibleCells().map(cell => (
                    <div
                      key={cell.id}
                      style={{
                        width: cell.column.columnDef.size,
                        flex: cell.column.columnDef.size ? "0 0 auto" : "1 1 0%",
                      }}
                      className="min-w-0 truncate px-2"
                    >
                      {flexRender(cell.column.columnDef.cell, cell.getContext())}
                    </div>
                  ))}
                </div>
              )
            })}
          </div>
        </div>
      )}
    </div>
  )
}
