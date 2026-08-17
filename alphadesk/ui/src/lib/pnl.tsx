import { cn } from "@/lib/utils"

/** Text color for a signed value — "" (no tint) for null/undefined/exact
 * zero, matching the tone convention every gain/loss display in the app
 * already followed before this was centralized. */
export function pnlClass(tone: number | null | undefined): string {
  if (tone == null || tone === 0) return ""
  return tone > 0 ? "text-gain" : "text-loss"
}

/** "+$12.34" / "-$12.34" — note the sign/currency ORDER: a naive
 * `$${v.toFixed(2)}` on a negative renders "$-12.34", not "-$12.34". */
export function fmtUsd(v: number, decimals = 2): string {
  const sign = v >= 0 ? "+$" : "-$"
  return `${sign}${Math.abs(v).toFixed(decimals)}`
}

export function fmtPct(v: number, decimals = 2): string {
  const sign = v >= 0 ? "+" : ""
  return `${sign}${v.toFixed(decimals)}%`
}

export function Pnl({
  value,
  format = "pct",
  decimals,
  className,
}: {
  value: number | null | undefined
  format?: "pct" | "usd"
  decimals?: number
  className?: string
}) {
  if (value == null) return <span className={cn("text-muted-foreground", className)}>—</span>
  const text = format === "usd" ? fmtUsd(value, decimals) : fmtPct(value, decimals)
  return <span className={cn(pnlClass(value), className)}>{text}</span>
}
