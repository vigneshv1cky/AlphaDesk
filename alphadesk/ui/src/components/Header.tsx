import { useEffect, useState } from "react"
import { api, type Stats } from "@/lib/api"
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip"
import { Separator } from "@/components/ui/separator"
import { pnlClass } from "@/lib/pnl"

function Stat({ label, value, tone, tip }: { label: string; value: string; tone?: number | null; tip?: string }) {
  const color = pnlClass(tone)
  const inner = (
    <div className="flex flex-col items-center gap-0.5">
      <div className="text-[10px] font-medium uppercase tracking-widest text-muted-foreground">{label}</div>
      <div className={`font-mono text-sm font-bold tabular-nums ${color}`}>{value}</div>
    </div>
  )
  if (!tip) return inner
  return <Tooltip><TooltipTrigger>{inner}</TooltipTrigger><TooltipContent side="bottom" className="max-w-[200px] text-xs">{tip}</TooltipContent></Tooltip>
}

export function Header({ liveOpenCount }: { liveOpenCount?: number }) {
  const [stats, setStats] = useState<Stats | null>(null)
  useEffect(() => {
    api.stats().then(setStats).catch(() => {})
    const i = setInterval(() => api.stats().then(setStats).catch(() => {}), 60_000)
    return () => clearInterval(i)
  }, [])

  const t = stats?.total
  const graded = t?.graded ?? 0
  const winRate = graded > 0 && t?.wins != null ? Math.round((t.wins / graded) * 100) : null
  const picks = t?.picks ?? 0

  return (
    <div className="flex items-center gap-0">
      <Stat label="Open" value={String(liveOpenCount ?? 0)} tip="Positions live with real-time P&L" />
      <Separator orientation="vertical" className="mx-2 h-8" />
      <Stat label="Graded" value={String(graded)} tip="Picks whose 1-day horizon elapsed" />
      <Separator orientation="vertical" className="mx-2 h-8" />
      <Stat label="Win Rate" value={winRate != null ? `${winRate}%` : "—"} tone={winRate != null ? (winRate >= 50 ? 1 : -1) : null} />
      <Separator orientation="vertical" className="mx-2 h-8" />
      <Stat label="Total" value={String(picks)} tip="All picks ever booked" />
    </div>
  )
}
