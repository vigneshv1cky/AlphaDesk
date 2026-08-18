import { useEffect, useState } from "react"
import { api, type Stats } from "@/lib/api"
import { pnlClass } from "@/lib/pnl"

/** The header readout — a compact ticker strip, not a row of cards. Values are
 * monospace so they don't jitter as they update. */
function Cell({ label, value, tone }: { label: string; value: string; tone?: number | null }) {
  return (
    <div className="flex shrink-0 items-baseline gap-1 border-l border-border px-2 first:border-l-0">
      <span className="text-[9px] uppercase tracking-[0.06em] text-muted-foreground">{label}</span>
      <span className={`num text-[11px] font-semibold ${pnlClass(tone)}`}>{value}</span>
    </div>
  )
}

export function Header({ liveOpenCount }: { liveOpenCount?: number }) {
  const [stats, setStats] = useState<Stats | null>(null)
  useEffect(() => {
    const load = () => api.stats().then(setStats).catch(() => {})
    load()
    const i = setInterval(load, 60_000)
    return () => clearInterval(i)
  }, [])

  const t = stats?.total
  const graded = t?.graded ?? 0
  const winRate = graded > 0 && t?.wins != null ? Math.round((t.wins / graded) * 100) : null

  return (
    <div className="flex items-center">
      <Cell label="open" value={String(liveOpenCount ?? 0)} />
      <Cell label="graded" value={String(graded)} />
      <Cell label="win" value={winRate != null ? `${winRate}%` : "—"} tone={winRate != null ? (winRate >= 50 ? 1 : -1) : null} />
      <Cell label="picks" value={String(t?.picks ?? 0)} />
    </div>
  )
}
