import * as React from "react"
import { cn } from "@/lib/utils"

/** The terminal primitives — hand-rolled, dependency-free replacements for
 * the shadcn/ui set that used to live in components/ui/.
 *
 * Sized to match AlphaSpace after measuring it: 14px cell type and ~33px rows,
 * not the 11px/24px this used to run. Measuring was the point — the assumption
 * had been that they were denser than us, and the reverse was true, so "more
 * terminal-like" was costing legibility for nothing.
 *
 * Still no shadows: separation comes from the four surface steps in index.css,
 * which is where their board actually gets its depth. Nothing animates,
 * because motion in a data grid is noise unless it encodes a change in the
 * data.
 *
 * Primitives carry `data-slot`, the same hook they use — it names the part in
 * the DOM, which makes both theme overrides and tests target intent rather
 * than a class string that will drift. */

/* ── Sparkline: a row-scale trend, not a chart ──────────────────────────── */

/** A bare trend line sized for a table cell.
 *
 * Inline SVG rather than a charting library: at 64x18 there is no axis, no
 * scale and no interaction to justify one, and this renders dozens of times
 * per movers table. Stroke is `currentColor`, so the caller tints it with a
 * gain/loss text class and it stays correct in both themes for free.
 *
 * Renders nothing at all below two points. A single point would draw a flat
 * line, which reads as "this did not move" rather than the truth, "we do not
 * have the data" — the same distinction the chart's indicator gate makes. */
export function Sparkline({
  points, className, width = 64, height = 18,
}: {
  points: number[]
  className?: string
  width?: number
  height?: number
}) {
  if (!points || points.length < 2) {
    return <span className="inline-block" style={{ width, height }} aria-hidden="true" />
  }
  const min = Math.min(...points)
  const max = Math.max(...points)
  const span = max - min
  // Inset by the stroke width so the extremes are not clipped at the edges.
  const pad = 1
  const h = height - pad * 2
  const d = points
    .map((p, i) => {
      const x = (i / (points.length - 1)) * width
      // A flat series has no span to scale against. Centre it rather than
      // letting the divide-by-zero guard pin it to the floor — a line along
      // the bottom edge reads as a collapse, which is the opposite of what
      // "unchanged" means.
      const y = span === 0 ? height / 2 : pad + h - ((p - min) / span) * h
      return `${i ? "L" : "M"}${x.toFixed(1)},${y.toFixed(1)}`
    })
    .join(" ")
  return (
    <svg
      width={width}
      height={height}
      viewBox={`0 0 ${width} ${height}`}
      className={cn("overflow-visible", className)}
      role="img"
      aria-label="recent trend"
    >
      <path d={d} fill="none" stroke="currentColor" strokeWidth="1" vectorEffect="non-scaling-stroke" />
    </svg>
  )
}

/* ── Widget: the tiled panel every surface is built from ────────────────── */

/** An expanded tile gets height as well as width. Width alone does nothing for
 * the tiles that are actually too small — a quote list or a headline feed is
 * long, not wide, and stretching it sideways only shortens the scroll it was
 * already doing. Lives here rather than in widgets/tile.ts because Widget owns
 * its own body box, and a primitive importing from widgets/ would point the
 * dependency arrow the wrong way. */
const EXPANDED_BODY_HEIGHT = 760

/** Titles are ReactNode, but an aria-label has to be a string. Anything that is
 * not plain text falls back to a generic word rather than "[object Object]". */
function titleText(title: React.ReactNode): string {
  return typeof title === "string" ? title : "panel"
}

export function Widget({
  title, symbol, subtitle, actions, span = 12, className, bodyClassName, scroll, children,
  expanded, onExpandChange, expandable = true,
}: {
  title?: React.ReactNode
  /** Rendered in accent blue before the title, the way AlphaSpace prefixes a
   * widget with the symbol it is scoped to ("NVDA EQUITY OVERVIEW"). */
  symbol?: string
  subtitle?: React.ReactNode
  actions?: React.ReactNode
  /** Columns to span on the 12-col `.collage` grid. Ignored off-grid. */
  span?: number
  className?: string
  bodyClassName?: string
  /** Fixed body height with internal scroll — keeps one long widget from
   * stretching its whole grid row. */
  scroll?: number | string
  /** Expansion state, when the OWNER needs it. A widget whose body changes
   * shape on expand has to know — the chart grows its price pane and only
   * offers RSI/MACD once there is height to read them — and it must be the
   * same state its own toolbar button drives, or the two controls disagree
   * about whether the tile is open. Omit both and the tile expands on its own. */
  expanded?: boolean
  onExpandChange?: (next: boolean) => void
  /** Opt out for a tile the header control makes no sense on. */
  expandable?: boolean
  children?: React.ReactNode
}) {
  // Uncontrolled fallback, so a tile with nothing to coordinate still expands
  // without its parent holding state it otherwise has no use for.
  const [selfExpanded, setSelfExpanded] = React.useState(false)
  const isOpen = expanded ?? selfExpanded
  const toggle = () =>
    expanded === undefined ? setSelfExpanded(v => !v) : onExpandChange?.(!expanded)

  const cols = isOpen ? 12 : span
  // A caller that drops `scroll` when open (the chart, whose canvas sizes
  // itself) keeps that behaviour; a fixed-height body just gets taller.
  const bodyHeight = isOpen && typeof scroll === "number" ? EXPANDED_BODY_HEIGHT : scroll
  return (
    <section
      data-slot="widget"
      data-expanded={isOpen || undefined}
      // Inline gridColumn, not a Tailwind class: `col-span-${n}` is built at
      // runtime and would be purged from the stylesheet.
      style={{ gridColumn: `span ${cols} / span ${cols}` }}
      className={cn(
        "flex min-w-0 flex-col overflow-hidden rounded-lg border border-border bg-panel",
        className,
      )}
    >
      {(title || actions) && (
        <header data-slot="widget-header" className="flex h-[38px] shrink-0 items-center gap-2 border-b border-grid-line bg-panel-header px-3">
          {symbol && (
            <span className="shrink-0 text-[15px] font-semibold text-accent">{symbol}</span>
          )}
          {title && (
            <h2 className="truncate text-[14px] font-semibold uppercase tracking-[0.06em] text-foreground/90">
              {title}
            </h2>
          )}
          {subtitle && (
            <span className="truncate text-[14px] text-muted-foreground">{subtitle}</span>
          )}
          <div className="flex-1" />
          {actions}
          {expandable && (
            <button
              type="button"
              onClick={toggle}
              aria-expanded={isOpen}
              aria-label={isOpen ? `Collapse ${titleText(title)}` : `Expand ${titleText(title)}`}
              title={isOpen ? "Collapse" : "Expand to full width"}
              className="shrink-0 text-[14px] leading-none text-muted-foreground/40 transition-colors hover:text-foreground"
            >
              {isOpen ? "⤡" : "⤢"}
            </button>
          )}
        </header>
      )}
      <div
        style={bodyHeight ? { height: typeof bodyHeight === "number" ? `${bodyHeight}px` : bodyHeight } : undefined}
        className={cn("min-h-0 min-w-0", bodyHeight && "overflow-y-auto", bodyClassName)}
      >
        {children}
      </div>
    </section>
  )
}

/* ── Controls ───────────────────────────────────────────────────────────── */

export function Btn({
  variant = "default", active, className, ...props
}: React.ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: "default" | "ghost" | "accent"
  active?: boolean
}) {
  return (
    <button
      data-slot="button"
      {...props}
      className={cn(
        "inline-flex h-[28px] shrink-0 items-center gap-1 whitespace-nowrap rounded-md px-2.5 text-[14px] font-medium transition-colors disabled:pointer-events-none disabled:opacity-40",
        variant === "default" && "border border-border bg-transparent hover:bg-muted",
        variant === "ghost" && "text-muted-foreground hover:bg-muted hover:text-foreground",
        variant === "accent" && "bg-accent text-accent-foreground hover:opacity-90",
        active && "bg-accent text-accent-foreground",
        className,
      )}
    />
  )
}

/* No width here on purpose. Without tailwind-merge a caller's `w-24` does NOT
 * reliably beat a base `w-full` — both classes ship and CSS order decides, so
 * a base width silently wins and every field eats its own row. Callers state
 * their own width (`w-24`, `flex-1`, `w-full`). */
export const fieldCls =
  "h-[30px] rounded-md border border-input bg-background px-1.5 text-[14px] text-foreground placeholder:text-muted-foreground/60 focus:border-ring focus:outline-none"

export const areaCls =
  "resize-y rounded-md border border-input bg-background px-1.5 py-1 text-[14px] leading-snug text-foreground placeholder:text-muted-foreground/60 focus:border-ring focus:outline-none"

export function Tag({
  tone = "neutral", className, children,
}: {
  tone?: "neutral" | "gain" | "loss" | "accent"
  className?: string
  children: React.ReactNode
}) {
  return (
    <span
      className={cn(
        "inline-flex items-center whitespace-nowrap border px-1 text-[11px] font-semibold uppercase leading-[14px] tracking-[0.06em]",
        tone === "neutral" && "border-border text-muted-foreground",
        tone === "gain" && "border-gain/40 text-gain",
        tone === "loss" && "border-loss/40 text-loss",
        tone === "accent" && "border-accent/50 text-accent",
        className,
      )}
    >
      {children}
    </span>
  )
}

export function Shimmer({ className }: { className?: string }) {
  return <div className={cn("animate-pulse bg-muted", className)} />
}

/** Native <details> instead of a JS collapsible — no dependency, keyboard and
 * find-in-page work for free. */

/* ── Dense table ────────────────────────────────────────────────────────── */

export function Table({ className, children }: { className?: string; children: React.ReactNode }) {
  return (
    <div className="w-full overflow-x-auto">
      <table data-slot="table" className={cn("w-full border-collapse text-[14px]", className)}>{children}</table>
    </div>
  )
}

export function THead({ children }: { children: React.ReactNode }) {
  return (
    // sticky so a scrolling widget body keeps its column labels
    <thead className="sticky top-0 z-10 bg-panel-header">
      <tr className="border-b border-border">{children}</tr>
    </thead>
  )
}

export function TH({
  align = "left", className, children,
}: { align?: "left" | "right" | "center"; className?: string; children?: React.ReactNode }) {
  return (
    <th
      className={cn(
        // Measured off their board: 10px at weight 500 with 1px tracking and
        // 14px of vertical air. Sticky, so scrolling a 250-row window keeps
        // the column names — theirs does this and it is why long lists stay
        // readable without a rule under every row.
        "sticky top-0 z-10 whitespace-nowrap bg-panel px-[12px] py-[14px] text-[10px] font-medium uppercase tracking-[1px] text-muted-foreground",
        align === "left" && "text-left",
        align === "right" && "text-right",
        align === "center" && "text-center",
        className,
      )}
    >
      {children}
    </th>
  )
}

export function TR({
  className, onClick, children,
}: { className?: string; onClick?: () => void; children: React.ReactNode }) {
  return (
    <tr
      onClick={onClick}
      className={cn(
        // No border. Theirs draws nothing between rows — 33px of height and a
        // hover wash do the separating. Ruling every row is what made this
        // read as a spreadsheet rather than a board.
        "hover:bg-muted/60",
        onClick && "cursor-pointer",
        className,
      )}
    >
      {children}
    </tr>
  )
}

export function TD({
  align = "left", mono, className, colSpan, children,
}: {
  align?: "left" | "right" | "center"
  /** Tabular monospace — use for every numeric column so digits align. */
  mono?: boolean
  className?: string
  colSpan?: number
  children?: React.ReactNode
}) {
  return (
    <td
      colSpan={colSpan}
      className={cn(
        "h-[33px] whitespace-nowrap px-[12px] py-[6px]",
        align === "left" && "text-left",
        align === "right" && "text-right",
        align === "center" && "text-center",
        mono && "num",
        className,
      )}
    >
      {children}
    </td>
  )
}

/** Centered filler for an empty/loading/error widget body. */
export function Empty({ children }: { children: React.ReactNode }) {
  return (
    <div className="px-2 py-4 text-center text-[14px] text-muted-foreground">{children}</div>
  )
}

/** Label/value pair — the stat readout used across the dashboard tiles. */
export function Stat({
  label, value, tone, sub,
}: {
  label: string
  value: React.ReactNode
  tone?: "gain" | "loss"
  sub?: React.ReactNode
}) {
  return (
    <div className="min-w-0 border-r border-grid-line px-2 py-1.5 last:border-r-0">
      <div className="truncate text-[11px] uppercase tracking-[0.06em] text-muted-foreground">{label}</div>
      <div
        className={cn(
          "num truncate text-[15px] leading-tight",
          tone === "gain" && "text-gain",
          tone === "loss" && "text-loss",
        )}
      >
        {value}
      </div>
      {sub && <div className="truncate text-[11px] text-muted-foreground">{sub}</div>}
    </div>
  )
}

/* ── Dense drop-ins for the removed shadcn/ui surface ───────────────────────
 *
 * These keep the shadcn NAMES and prop shapes so the call sites that already
 * work (Earnings' 520 lines of calendar logic, the performance tables) didn't
 * have to be rewritten to delete a dependency. They are not a component
 * library: no variant engine, no polymorphic `render`, no portals — just
 * functions that render flat, dense markup.
 *
 * Only the ones with real callers survive: the Card/CardContent/Skeleton/
 * Separator shims were deleted once every page had been converted, since
 * keeping unused wrappers would just be a component library by another name.
 */

const BADGE_TONE: Record<string, string> = {
  default: "border-accent/50 bg-accent/15 text-accent",
  secondary: "border-border bg-muted text-muted-foreground",
  destructive: "border-loss/40 bg-loss/10 text-loss",
  outline: "border-border text-foreground",
  ghost: "border-transparent text-muted-foreground",
}

export function Badge({
  variant = "default", className, children,
}: { variant?: string; className?: string; children?: React.ReactNode }) {
  return (
    <span
      className={cn(
        "inline-flex items-center whitespace-nowrap border px-1 text-[11px] font-semibold uppercase leading-[14px] tracking-[0.06em]",
        BADGE_TONE[variant] ?? BADGE_TONE.default,
        className,
      )}
    >
      {children}
    </span>
  )
}

export function Button({
  variant = "default", className, ...props
}: React.ButtonHTMLAttributes<HTMLButtonElement> & { variant?: string }) {
  return (
    <button
      {...props}
      className={cn(
        "inline-flex items-center justify-center gap-1 whitespace-nowrap text-[14px] font-medium transition-colors disabled:pointer-events-none disabled:opacity-40",
        // `h-auto` in a caller's className must win, so height is only applied
        // when the caller hasn't asked for its own.
        !className?.includes("h-auto") && "h-[22px]",
        "px-2",
        variant === "ghost"
          ? "text-muted-foreground hover:bg-muted hover:text-foreground"
          : variant === "outline"
            ? "border border-border hover:bg-muted"
            : "bg-accent text-accent-foreground hover:opacity-90",
        className,
      )}
    />
  )
}

/** thead only — TableRow supplies the row, matching shadcn's composition. */
export function TableHeader({ children }: { children: React.ReactNode }) {
  return <thead className="sticky top-0 z-10 bg-panel-header">{children}</thead>
}

export function TableBody({ children }: { children: React.ReactNode }) {
  return <tbody>{children}</tbody>
}

export { TR as TableRow, TD as TableCell, TH as TableHead }
