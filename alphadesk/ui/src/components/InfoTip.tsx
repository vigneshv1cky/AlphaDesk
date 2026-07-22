import type { ReactNode } from "react"
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip"

// Thin wrapper: a styled hover tooltip on an inline element. Replaces the native
// `title=` attribute with the proper Base UI tooltip (positioned, animated,
// consistent) without a full <Tooltip>/<Trigger>/<Content> dance at each site.
export function InfoTip({
  tip,
  children,
  className,
}: {
  tip: string
  children: ReactNode
  className?: string
}) {
  return (
    <Tooltip>
      <TooltipTrigger render={<span className={className} />}>{children}</TooltipTrigger>
      <TooltipContent>{tip}</TooltipContent>
    </Tooltip>
  )
}
