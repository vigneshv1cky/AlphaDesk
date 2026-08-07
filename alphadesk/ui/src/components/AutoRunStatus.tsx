import { useEffect, useState } from "react"
import { Clock, Zap } from "lucide-react"
import { Card, CardContent } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"

export function AutoRunStatus() {
  const [lastRun, setLastRun] = useState<string | null>(null)

  useEffect(() => {
    const poll = async () => {
      try {
        const r = await fetch("/api/stats")
        if (r.ok) {
          const s = await r.json()
          setLastRun(s?.total?.last_run || null)
        }
      } catch {}
    }
    poll()
    const i = setInterval(poll, 60_000)
    return () => clearInterval(i)
  }, [])

  const timeAgo = lastRun
    ? (() => {
        const d = new Date(lastRun)
        if (isNaN(d.getTime())) return null
        const ms = Date.now() - d.getTime()
        const min = Math.floor(ms / 60_000)
        if (min < 1) return "just now"
        if (min < 60) return `${min}m ago`
        return `${Math.floor(min / 60)}h ${min % 60}m ago`
      })()
    : null

  return (
    <Card>
      <CardContent className="py-4">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Zap className="h-4 w-4 text-emerald-500" />
            <h2 className="text-sm font-semibold tracking-tight">Auto-Run</h2>
          </div>
          <div className="flex items-center gap-2 text-xs text-muted-foreground">
            <Clock className="h-3 w-3" />
            {timeAgo ? (
              <span>Last run: {timeAgo}</span>
            ) : (
              <span>Waiting…</span>
            )}
          </div>
        </div>
        <p className="mt-2 text-xs text-muted-foreground">
          <Badge className="mr-1 gap-1 rounded-full bg-emerald-500/10 text-emerald-600 dark:text-emerald-400">
            <span className="h-1.5 w-1.5 rounded-full bg-emerald-500" />
            Active
          </Badge>
          Scans hourly 04:00–19:00 ET
        </p>
      </CardContent>
    </Card>
  )
}
