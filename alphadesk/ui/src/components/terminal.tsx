import * as React from "react"
import { cn } from "@/lib/utils"

/** The terminal primitives — hand-rolled, dependency-free replacements for
 * the shadcn/ui set that used to live in components/ui/.
 *
 * The whole point is DENSITY. Every default here is tuned to put more real
 * data on screen than a general-purpose component library would: 24px table
 * rows, 11px type, 1px borders instead of shadows, square corners instead of
 * radii, and headers that cost 26px rather than a padded CardHeader. Nothing
 * animates, because motion in a data grid is noise unless it encodes a
 * change in the data. */

/* ── Widget: the tiled panel every surface is built from ────────────────── */

export function Widget({
  title, subtitle, actions, span = 12, className, bodyClassName, scroll, children,
}: {
  title?: React.ReactNode
  subtitle?: React.ReactNode
  actions?: React.ReactNode
  /** Columns to span on the 12-col `.collage` grid. Ignored off-grid. */
  span?: number
  className?: string
  bodyClassName?: string
  /** Fixed body height with internal scroll — keeps one long widget from
   * stretching its whole grid row. */
  scroll?: number | string
  children?: React.ReactNode
}) {
  return (
    <section
      // Inline gridColumn, not a Tailwind class: `col-span-${n}` is built at
      // runtime and would be purged from the stylesheet.
      style={{ gridColumn: `span ${span} / span ${span}` }}
      className={cn("flex min-w-0 flex-col bg-panel", className)}
    >
      {(title || actions) && (
        <header className="flex h-[26px] shrink-0 items-center gap-2 border-b border-border bg-panel-header px-2">
          {title && (
            <h2 className="truncate text-[10px] font-semibold uppercase tracking-[0.08em] text-foreground/80">
              {title}
            </h2>
          )}
          {subtitle && (
            <span className="truncate text-[10px] text-muted-foreground">{subtitle}</span>
          )}
          <div className="flex-1" />
          {actions}
        </header>
      )}
      <div
        style={scroll ? { height: typeof scroll === "number" ? `${scroll}px` : scroll } : undefined}
        className={cn("min-h-0 min-w-0", scroll && "overflow-y-auto", bodyClassName)}
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
      {...props}
      className={cn(
        "inline-flex h-[22px] shrink-0 items-center gap-1 whitespace-nowrap px-2 text-[11px] font-medium transition-colors disabled:pointer-events-none disabled:opacity-40",
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
  "h-[22px] border border-input bg-background px-1.5 text-[11px] text-foreground placeholder:text-muted-foreground/60 focus:border-ring focus:outline-none"

export const areaCls =
  "resize-y border border-input bg-background px-1.5 py-1 text-[11px] leading-snug text-foreground placeholder:text-muted-foreground/60 focus:border-ring focus:outline-none"

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
        "inline-flex items-center whitespace-nowrap border px-1 text-[9px] font-semibold uppercase leading-[14px] tracking-[0.06em]",
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
      <table className={cn("w-full border-collapse text-[11px]", className)}>{children}</table>
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
        "h-[22px] whitespace-nowrap px-2 text-[9px] font-semibold uppercase tracking-[0.06em] text-muted-foreground",
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
        "border-b border-grid-line last:border-b-0 hover:bg-muted/60",
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
        "h-[24px] whitespace-nowrap px-2",
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
    <div className="px-2 py-4 text-center text-[11px] text-muted-foreground">{children}</div>
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
      <div className="truncate text-[9px] uppercase tracking-[0.06em] text-muted-foreground">{label}</div>
      <div
        className={cn(
          "num truncate text-[15px] leading-tight",
          tone === "gain" && "text-gain",
          tone === "loss" && "text-loss",
        )}
      >
        {value}
      </div>
      {sub && <div className="truncate text-[9px] text-muted-foreground">{sub}</div>}
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
        "inline-flex items-center whitespace-nowrap border px-1 text-[9px] font-semibold uppercase leading-[14px] tracking-[0.06em]",
        BADGE_TONE[variant] ?? BADGE_TONE.default,
        className,
      )}
    >
      {children}
    </span>
  )
}

export function Button({
  variant = "default", size, className, ...props
}: React.ButtonHTMLAttributes<HTMLButtonElement> & { variant?: string; size?: string }) {
  return (
    <button
      {...props}
      className={cn(
        "inline-flex items-center justify-center gap-1 whitespace-nowrap text-[11px] font-medium transition-colors disabled:pointer-events-none disabled:opacity-40",
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
