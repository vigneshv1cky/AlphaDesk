import { useEffect, useState } from "react"
import { api, fmtAlpha, type SessionAgg, type SessionPick, type SessionsResponse } from "@/lib/api"
import { dirUp, dirWord, plainEdge, plainVerdict } from "@/lib/plain"
import { Badge } from "@/components/ui/badge"
import { ArrowDown, ArrowUp } from "lucide-react"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"

// The three market windows the desk operates in — every pick is stamped with the
// window it was DECIDED in, and open positions carry over inside their window.
const WINDOWS: { key: keyof SessionsResponse["sessions"]; label: string; hint: string }[] = [
  { key: "day", label: "Day market", hint: "regular hours (9:30–16:00 ET)" },
  { key: "extended", label: "Extended", hint: "pre + after-hours" },
  { key: "night", label: "Night", hint: "overnight / weekend — fills at next open" },
]

function statusOf(p: SessionPick): { text: string; cls: string } {
  if (p.exit_ts)
    return { text: `Exited ${fmtAlpha(p.exit_return_pct)}`, cls: "bg-zinc-500 text-white" }
  if (p.graded_at && p.alpha_net != null)
    return {
      text: fmtAlpha(p.alpha_net),
      cls: p.alpha_net > 0 ? "bg-emerald-600 text-white" : "bg-red-600 text-white",
    }
  if (p.entry_price == null) return { text: "Fills at open", cls: "bg-amber-500 text-white" }
  return { text: "Open", cls: "bg-emerald-600 text-white" }
}

function StatLine({ a }: { a: SessionAgg }) {
  const win = a.graded > 0 ? Math.round((100 * a.wins) / a.graded) : null
  return (
    <div className="flex flex-wrap gap-x-3 gap-y-0.5 text-xs text-muted-foreground">
      <span>{a.n} picks</span>
      <span>
        {a.graded} graded{win != null ? ` · ${win}% beat S&P` : ""}
        {a.avg_alpha != null ? ` · avg ${fmtAlpha(a.avg_alpha)}` : ""}
      </span>
    </div>
  )
}

function Row({ p, onSelect }: { p: SessionPick; onSelect: (id: number) => void }) {
  const st = statusOf(p)
  const up = dirUp(p.direction)
  return (
    <button
      onClick={() => onSelect(p.id)}
      className="w-full rounded-md border bg-card p-2.5 text-left text-sm transition-colors hover:border-indigo-400"
    >
      <div className="flex flex-wrap items-center gap-2">
        {up ? (
          <ArrowUp className="h-4 w-4 text-emerald-600 dark:text-emerald-400" />
        ) : (
          <ArrowDown className="h-4 w-4 text-red-600 dark:text-red-400" />
        )}
        <span className={up ? "font-bold text-emerald-600 dark:text-emerald-400" : "font-bold text-red-600 dark:text-red-400"}>
          {dirWord(p.direction)}
        </span>
        <span className="font-bold">{p.symbol}</span>
        <Badge variant="secondary">{plainEdge(p.edge)}</Badge>
        {p.verdict && <span className="text-muted-foreground">{plainVerdict(p.verdict)}</span>}
        <span className="text-muted-foreground">conf {Math.round(p.adjusted_score ?? p.confidence)}</span>
        <span className="text-muted-foreground">~{p.horizon_days}d</span>
        <Badge className={`ml-auto ${st.cls}`}>{st.text}</Badge>
      </div>
      {p.plan_entry != null && p.plan_target != null && p.plan_stop != null && (
        <p className="mt-1 text-xs text-muted-foreground">
          {up ? "Buy" : "Short"} ${p.plan_entry} · target ${p.plan_target} · stop ${p.plan_stop}
        </p>
      )}
      {p.exit_reason && <p className="mt-1 text-xs text-muted-foreground">{p.exit_reason}</p>}
    </button>
  )
}

export function Sessions({ onSelect }: { onSelect: (id: number) => void }) {
  const [data, setData] = useState<SessionsResponse | null>(null)
  useEffect(() => {
    const load = () => api.sessions(14).then(setData).catch(console.error)
    load()
    const t = setInterval(load, 60_000)
    return () => clearInterval(t)
  }, [])

  const trigger =
    "px-3 text-sm data-active:bg-indigo-600 data-active:text-white dark:data-active:border-transparent dark:data-active:bg-indigo-600 dark:data-active:text-white"
  return (
    <Tabs defaultValue="day" className="gap-3">
      <TabsList className="h-9 bg-card p-1">
        {WINDOWS.map((w) => (
          <TabsTrigger key={w.key} value={w.key} className={trigger}>
            {w.label}
            {data ? ` (${data.agg[w.key].n})` : ""}
          </TabsTrigger>
        ))}
      </TabsList>
      {WINDOWS.map((w) => (
        <TabsContent key={w.key} value={w.key} className="space-y-2">
          <div className="flex items-center justify-between gap-2">
            <StatLine a={data ? data.agg[w.key] : { n: 0, open: 0, graded: 0, wins: 0, avg_alpha: null }} />
            <span className="text-[11px] text-muted-foreground">{w.hint}</span>
          </div>
          {(data?.sessions[w.key] ?? []).map((p) => (
            <Row key={p.id} p={p} onSelect={onSelect} />
          ))}
          {data && data.sessions[w.key].length === 0 && (
            <p className="text-sm text-muted-foreground">No picks in this window yet.</p>
          )}
        </TabsContent>
      ))}
    </Tabs>
  )
}
