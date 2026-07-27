import { useEffect, useState } from "react"
import { api, etDateTime, exitDate, fmtAlpha, type Pick } from "@/lib/api"
import { dirWord, plainEdge, plainVerdict } from "@/lib/plain"
import { ArrowDown, ArrowUp, ChevronDown, ChevronRight, X } from "lucide-react"

function Brief({ b }: { b: { kind: string; summary: string; key_facts?: (string | { fact: string })[] } }) {
  const [open, setOpen] = useState(false)
  const facts = b.key_facts ?? []
  const labels: Record<string, string> = { market: "Market", news: "News", earnings: "Earnings" }
  return (
    <div className="text-xs">
      <button
        onClick={() => setOpen(!open)}
        className="flex items-center gap-1 text-zinc-400 hover:text-zinc-200 w-full text-left"
      >
        {open ? <ChevronDown className="h-3 w-3 shrink-0" /> : <ChevronRight className="h-3 w-3 shrink-0" />}
        <span className="font-semibold">{labels[b.kind] ?? b.kind}</span>
        <span className="text-zinc-600 truncate">— {b.summary.slice(0, 100)}{b.summary.length > 100 ? "…" : ""}</span>
      </button>
      {open && (
        <div className="mt-1 ml-4 space-y-0.5 text-zinc-500">
          {facts.map((f, j) => (
            <div key={j}>· {typeof f === "string" ? f : f.fact}</div>
          ))}
        </div>
      )}
    </div>
  )
}

export function PickSheet({
  pickId,
  onClose,
}: {
  pickId: number | null
  onClose: () => void
}) {
  const [pick, setPick] = useState<Pick | null>(null)

  useEffect(() => {
    setPick(null)
    if (pickId !== null) {
      api.pick(pickId).then(setPick).catch(console.error)
    }
  }, [pickId])

  useEffect(() => {
    if (pickId === null) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose()
    }
    document.addEventListener("keydown", onKey)
    document.body.style.overflow = "hidden"
    return () => {
      document.removeEventListener("keydown", onKey)
      document.body.style.overflow = ""
    }
  }, [pickId, onClose])

  if (pickId === null) return null

  const long = pick?.direction === "LONG"
  const dirColor = long ? "text-emerald-600 dark:text-emerald-400" : "text-red-600 dark:text-red-400"
  const DirIcon = long ? ArrowUp : ArrowDown

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-4"
      role="dialog"
      aria-modal="true"
    >
      <div
        className="absolute inset-0 bg-black/50 backdrop-blur-sm"
        onClick={onClose}
        aria-hidden="true"
      />
      <div className="relative z-10 flex max-h-[88vh] w-full max-w-2xl flex-col overflow-hidden rounded-lg border border-zinc-300 dark:border-zinc-700 bg-white dark:bg-zinc-950 shadow-2xl">
        {/* Header */}
        <div className="flex items-start justify-between gap-3 border-b border-zinc-200 dark:border-zinc-800 px-5 py-3">
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <DirIcon className={`h-5 w-5 shrink-0 ${dirColor}`} />
              <span className="font-mono text-sm text-zinc-500">#{pick?.id ?? pickId}</span>
              <span className={`font-bold text-lg ${dirColor}`}>{dirWord(pick?.direction ?? "").toUpperCase()}</span>
              <span className="font-bold text-lg text-zinc-900 dark:text-zinc-200">{pick?.symbol}</span>
            </div>
            <div className="mt-0.5 flex flex-wrap items-center gap-x-2 gap-y-0.5 text-xs text-zinc-500">
              <span>{pick?.arm === "LONER" ? "Loner" : "Team"}</span>
              {pick?.edge && <span>· {plainEdge(pick.edge)}</span>}
              <span>· {etDateTime(pick?.ts ?? "")} ET</span>
              <span>· {pick?.session === "PRE" ? "pre-market" : pick?.session === "AFTER" ? "after-hours" : pick?.session === "OPEN" ? "regular" : "entered at open"}</span>
            </div>
          </div>
          <button onClick={onClose} aria-label="Close" className="grid h-7 w-7 shrink-0 place-items-center rounded text-zinc-400 hover:text-zinc-900 dark:hover:text-zinc-200 transition-colors">
            <X className="h-4 w-4" />
          </button>
        </div>

        <div className="no-scrollbar min-h-0 flex-1 overflow-y-auto p-5 text-sm">
          {!pick ? (
            <div className="text-zinc-500 animate-pulse">Loading…</div>
          ) : (
            <div className="space-y-4">
              {/* ── Summary strip ── */}
              <div className="grid grid-cols-4 gap-3 text-center">
                {[
                  { label: "Entry", value: pick.entry_price ? `$${pick.entry_price}` : "next open" },
                  { label: "Target", value: pick.plan_target ? `$${pick.plan_target}` : "—" },
                  { label: "Stop", value: pick.plan_stop ? `$${pick.plan_stop}` : "—" },
                  {
                    label: "vs S&P",
                    value: pick.alpha_net != null ? fmtAlpha(pick.alpha_net) : "—",
                    color: (pick.alpha_net ?? 0) >= 0 ? "text-emerald-600 dark:text-emerald-400" : "text-red-600 dark:text-red-400",
                  },
                ].map((k) => (
                  <div key={k.label} className="rounded-lg bg-zinc-100 dark:bg-zinc-900 p-2">
                    <div className="text-[10px] uppercase tracking-wider text-zinc-500">{k.label}</div>
                    <div className={`font-mono font-bold ${(k as any).color ?? ""}`}>{k.value}</div>
                  </div>
                ))}
              </div>

              <div className="grid grid-cols-2 gap-2 text-center">
                <div className="rounded bg-zinc-100 dark:bg-zinc-900 px-2 py-1.5">
                  <span className="text-[10px] uppercase tracking-wider text-zinc-500">Confidence</span>
                  <div className="font-mono font-bold">{Math.round(pick.adjusted_score ?? pick.score)}/100</div>
                </div>
                <div className="rounded bg-zinc-100 dark:bg-zinc-900 px-2 py-1.5">
                  <span className="text-[10px] uppercase tracking-wider text-zinc-500">Horizon</span>
                  <div className="font-mono font-bold">~{pick.horizon_days}d · until {exitDate(pick.ts, pick.session, pick.horizon_days)}</div>
                </div>
              </div>

              {/* ── Scout reason ── */}
              {pick.triage_reason && (
                <div className="border-l-2 border-yellow-500 pl-3 py-0.5">
                  <div className="text-[10px] uppercase tracking-wider text-yellow-600 dark:text-yellow-400 mb-0.5">Why we looked</div>
                  <div className="text-zinc-600 dark:text-zinc-400 text-xs leading-relaxed">{pick.triage_reason}</div>
                </div>
              )}

              {/* ── Briefs — collapsed by default ── */}
              {(pick.briefs ?? []).length > 0 && (
                <div>
                  <div className="text-[10px] uppercase tracking-wider text-zinc-500 mb-2">Evidence</div>
                  <div className="space-y-1">
                    {(pick.briefs ?? []).map((b, i) => <Brief key={i} b={b} />)}
                  </div>
                </div>
              )}

              {/* ── Research flow ── */}
              <div className="space-y-3">
                {pick.thesis && (
                  <div className="border-l-2 border-blue-500 pl-3">
                    <div className="flex items-center gap-2 mb-1">
                      <span className="text-[10px] uppercase tracking-wider text-blue-600 dark:text-blue-400 font-semibold">Researcher</span>
                      {pick.debate?.flipped && <span className="text-[10px] text-fuchsia-400">· REVERSED by critic</span>}
                    </div>
                    <div className="text-zinc-800 dark:text-zinc-200 text-xs leading-relaxed">{pick.thesis}</div>
                    <div className="text-zinc-500 text-xs mt-1">confidence {Math.round(pick.score)}/100</div>
                  </div>
                )}

                {(pick.debate?.concerns ?? []).length > 0 && (
                  <div className="border-l-2 border-red-500 pl-3">
                    <div className="flex items-center gap-2 mb-1">
                      <span className="text-[10px] uppercase tracking-wider text-red-600 dark:text-red-400 font-semibold">Critic</span>
                      {pick.debate?.critic_stance === "FLIP" && (
                        <span className="text-[10px] text-fuchsia-400">
                          → {dirWord(pick.debate.counter_direction ?? "")}
                        </span>
                      )}
                    </div>
                    <div className="space-y-2">
                      {(pick.debate?.concerns ?? []).slice(0, 3).map((c, i) => (
                        <div key={i} className="text-xs">
                          <div className="text-zinc-800 dark:text-zinc-200 font-medium">{c.claim}</div>
                          {c.evidence && <div className="text-zinc-500 mt-0.5 line-clamp-3">{c.evidence}</div>}
                        </div>
                      ))}
                    </div>
                    {pick.debate?.counter && pick.debate.critic_stance !== "SUPPORT" && (
                      <div className="text-zinc-500 text-xs mt-2 italic">{pick.debate.counter}</div>
                    )}
                  </div>
                )}

                {pick.debate?.rebuttal && (
                  <div className="border-l-2 border-blue-500 pl-3 opacity-80">
                    <div className="text-[10px] uppercase tracking-wider text-blue-600 dark:text-blue-400 font-semibold mb-0.5">Reply</div>
                    <div className="text-zinc-600 dark:text-zinc-400 text-xs leading-relaxed">{pick.debate.rebuttal.rebuttal}</div>
                    <div className="text-zinc-500 text-xs mt-1">
                      score → {pick.debate.rebuttal.revised_score ?? pick.score}/100 · conceded: {pick.debate.rebuttal.concede ? "yes" : "no"}
                    </div>
                  </div>
                )}

                {pick.debate?.arbiter_summary && (
                  <div className="border-l-2 border-emerald-500 pl-3">
                    <div className="text-[10px] uppercase tracking-wider text-emerald-600 dark:text-emerald-400 font-semibold mb-0.5">Judge</div>
                    <div className="text-zinc-800 dark:text-zinc-200 text-xs leading-relaxed">{pick.debate.arbiter_summary}</div>
                    <div className="text-zinc-500 text-xs mt-1">
                      {plainVerdict(pick.verdict ?? "")} · confidence {pick.adjusted_score}/100
                      {pick.debate?.flipped && <span className="text-fuchsia-400"> · reversed</span>}
                    </div>
                  </div>
                )}
              </div>

              {/* ── Plan line ── */}
              {pick.plan_entry != null && pick.plan_target != null && pick.plan_stop != null && (
                <div className="text-xs text-zinc-500 pt-1 border-t border-zinc-200 dark:border-zinc-800">
                  {pick.plan_note && <span>"{pick.plan_note}" — </span>}
                  entry ${pick.plan_entry} · target ${pick.plan_target} · stop ${pick.plan_stop}
                </div>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
