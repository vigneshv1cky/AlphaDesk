import type { ReactNode } from "react"
import { cn } from "@/lib/utils"

/** Hover hint, CSS-only. The Base UI tooltip this replaced brought a provider,
 * a positioning engine and an animation layer for what is a one-line label on
 * a dense grid — and its portal/animation cost showed up on tables with dozens
 * of hinted cells. Positioned above the trigger and clipped to the viewport by
 * the widget's own overflow, which is good enough for a label. */
export function InfoTip({
  tip, children, className,
}: {
  tip: string
  children: ReactNode
  className?: string
}) {
  return (
    <span className={cn("group/tip relative inline-flex cursor-help", className)}>
      {children}
      <span
        role="tooltip"
        className="pointer-events-none absolute bottom-full left-1/2 z-50 mb-1 hidden -translate-x-1/2 whitespace-normal border border-border bg-popover px-1.5 py-1 text-[10px] font-normal normal-case leading-snug tracking-normal text-popover-foreground shadow-md group-hover/tip:block"
        style={{ width: "max-content", maxWidth: 220 }}
      >
        {tip}
      </span>
    </span>
  )
}
