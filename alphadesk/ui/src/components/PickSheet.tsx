import { useEffect, useState } from "react"
import { api, etDateTime, exitDate, fmtAlpha, type Pick } from "@/lib/api"
import { dirWord, plainEdge, plainVerdict } from "@/lib/plain"
import { ChevronDown, ChevronRight, X } from "lucide-react"

function Brief({ b }: { b: { kind: string; summary: string; key_facts?: (string | { fact: string })[] } }) {
  const [open, setOpen] = useState(false)
  const labels: Record<string, string> = { market: "Market", news: "News", earnings: "Earnings" }
  return (
    <div>
      <button onClick={() => setOpen(!open)} className="flex items-center gap-1 text-xs text-zinc-400 hover:text-zinc-200 w-full text-left">
        {open ? <ChevronDown className="h-3 w-3 shrink-0" /> : <ChevronRight className="h-3 w-3 shrink-0" />}
        <span className="font-semibold">{labels[b.kind] ?? b.kind}</span>
        <span className="text-zinc-600 truncate">— {b.summary.slice(0, 80)}{b.summary.length > 80 ? "…" : ""}</span>
      </button>
      {open && (
        <div className="mt-1 ml-4 text-xs text-zinc-500 space-y-0.5">
          {(b.key_facts ?? []).map((f, j) => (
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
      <div className="relative z-10 flex max-h-[88vh] w-full max-w-3xl flex-col overflow-hidden rounded-lg border border-zinc-300 dark:border-zinc-700 bg-white dark:bg-zinc-950 shadow-2xl">
        <div className="flex items-start justify-between gap-3 border-b border-zinc-700 px-4 py-3">
          <div className="min-w-0">
            <div className="font-mono text-sm">
              <span className="text-zinc-500">#{pick?.id ?? pickId}</span>
              {pick && (
                <>
                  {" "}
                  <span className={long ? "text-emerald-400" : "text-red-400"}>
                    {dirWord(pick.direction)}
                  </span>{" "}
                  <span className="font-bold text-zinc-900 dark:text-zinc-200">{pick.symbol}</span>
                  <span className="text-zinc-600"> · {pick.arm === "LONER" ? "Loner" : "Team"}</span>
                  {pick.edge && <span className="text-zinc-600"> · {plainEdge(pick.edge)}</span>}
                  <span className="text-zinc-600"> · {etDateTime(pick.ts)} ET</span>
                  <span className="text-zinc-600"> · {pick.session === "PRE" ? "pre-market" : pick.session === "AFTER" ? "after-hours" : pick.session === "OPEN" ? "regular hours" : "entered at open"}</span>
                </>
              )}
            </div>
          </div>
          <button
            onClick={onClose}
            aria-label="Close"
            className="grid h-7 w-7 shrink-0 place-items-center rounded text-zinc-500 transition-colors hover:bg-zinc-800 hover:text-zinc-900 dark:text-zinc-200"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        <div className="no-scrollbar min-h-0 flex-1 overflow-y-auto p-4 font-mono text-sm leading-relaxed">
          {!pick ? (
            <div className="text-zinc-500 animate-pulse">Loading…</div>
          ) : (
            <div className="space-y-3">
              {/* ── THE CALL ── */}
              <div>
                <div className="text-zinc-500 mb-1">── The Call ──</div>
                <div>
                  <span className={long ? "text-emerald-400" : "text-red-400"}>
                    [{dirWord(pick.direction).toUpperCase()}]
                  </span>{" "}
                  <span className="text-zinc-900 dark:text-zinc-200 font-bold">{pick.symbol}</span>
                  <span className="text-zinc-600"> · ~{pick.horizon_days}d until {exitDate(pick.ts, pick.session, pick.horizon_days)}</span>
                  <span className="text-zinc-600"> · conf {Math.round(pick.adjusted_score ?? pick.score)}</span>
                  {pick.verdict && <span className="text-zinc-600"> · {plainVerdict(pick.verdict)}</span>}
                  {pick.approved ? (
                    <span className="text-emerald-400"> · APPROVED</span>
                  ) : (
                    <span className="text-zinc-500"> · thin lean</span>
                  )}
                </div>
                <div className="text-zinc-500 text-xs mt-1 flex flex-wrap gap-x-3">
                  <span>
                    fill{" "}
                    {pick.entry_price ? (
                      <span>${pick.entry_price}{pick.session !== "OPEN" ? ` (${pick.session === "PRE" ? "pre-mkt" : pick.session === "AFTER" ? "after-hrs" : "entered at open"})` : ""}</span>
                    ) : (
                      "next open"
                    )}
                  </span>
                  {pick.plan_entry != null && pick.plan_target != null && pick.plan_stop != null && (
                    <>
                      <span>target <b className="text-emerald-600 dark:text-emerald-400">${pick.plan_target}</b></span>
                      <span>stop <b className="text-red-600 dark:text-red-400">${pick.plan_stop}</b></span>
                      {pick.plan_note && <span className="text-zinc-600">"{pick.plan_note}"</span>}
                    </>
                  )}
                </div>
                {pick.alpha_net !== null && (
                  <div className="text-zinc-500">
                    vs S&P: {fmtAlpha(pick.alpha_net)}
                    {pick.alpha_adj !== null && (
                      <span> · β-adj: {fmtAlpha(pick.alpha_adj)}{pick.beta != null ? ` (β ${pick.beta.toFixed(2)})` : ""}</span>
                    )}
                  </div>
                )}
              </div>

              <div className="border-t border-zinc-800" />

              {/* ── SCOUT ── */}
              {pick.triage_reason && (
                <div>
                  <div className="text-zinc-500 mb-1">── Why we looked ──</div>
                  <div>
                    <span className="text-yellow-400 font-semibold">[SCOUT]</span>{" "}
                    <span className="text-zinc-400">{pick.triage_reason}</span>
                  </div>
                </div>
              )}

              {/* ── BRIEFS ── */}
              {(pick.briefs ?? []).length > 0 && (
                <div>
                  <div className="text-zinc-500 mb-1">── Evidence ──</div>
                  <div className="space-y-1">
                    {(pick.briefs ?? []).map((b, i) => <Brief key={i} b={b} />)}
                  </div>
                </div>
              )}

              {/* ── THESIS ── */}
              {pick.thesis && (
                <div>
                  <div className="text-zinc-500 mb-1">── Researcher ──</div>
                  <div>
                    <span className="text-blue-400 font-semibold">[THESIS]</span>{" "}
                    <span className="text-zinc-800 dark:text-zinc-300">{pick.thesis}</span>
                  </div>
                  <div className="text-zinc-500 ml-4">
                    confidence {Math.round(pick.score)}/100 · ~{pick.horizon_days}d
                  </div>
                </div>
              )}

              {/* ── CRITIC ── */}
              {(pick.debate?.concerns ?? []).length > 0 && (
                <div>
                  <div className="text-zinc-500 mb-1">── Critic ──</div>
                  {(pick.debate?.concerns ?? []).map((c, i) => (
                    <div key={i}>
                      <span className="text-red-400 font-semibold">[CRITIC #{i + 1}]</span>{" "}
                      <span className="text-zinc-900 dark:text-zinc-200">{c.claim}</span>
                      <div className="text-zinc-500 ml-4">{c.evidence}</div>
                    </div>
                  ))}
                </div>
              )}

              {/* ── CRITIC STANCE ── */}
              {pick.debate?.critic_stance && pick.debate.critic_stance !== "SUPPORT" && (
                <div>
                  <span className="text-fuchsia-400 font-semibold">[CRITIC]</span>{" "}
                  {pick.debate.critic_stance === "FLIP" ? (
                    <span className="text-zinc-900 dark:text-zinc-200">
                      Reverse: {dirWord(pick.debate.proposed_direction)} →{" "}
                      <span className={pick.debate.counter_direction === "LONG" ? "text-emerald-400" : "text-red-400"}>
                        {dirWord(pick.debate.counter_direction)}
                      </span>
                    </span>
                  ) : (
                    <span className="text-zinc-900 dark:text-zinc-200">Stand aside — no edge either way</span>
                  )}
                  {pick.debate.counter && (
                    <div className="text-zinc-500 ml-4">{pick.debate.counter}</div>
                  )}
                </div>
              )}

              {/* ── FACT CHECK ── */}
              {(pick.debate?.fact_flags ?? []).length > 0 && (
                <div>
                  {(pick.debate?.fact_flags ?? []).map((f, i) => (
                    <div key={i}>
                      <span className="text-orange-400 font-semibold">[FACT]</span>{" "}
                      <span className="text-zinc-800 dark:text-zinc-300">{f}</span>
                    </div>
                  ))}
                </div>
              )}

              {/* ── REBUTTAL ── */}
              {pick.debate?.rebuttal && (
                <div>
                  <div className="text-zinc-500 mb-1">── Researcher's Reply ──</div>
                  <div>
                    <span className="text-blue-400 font-semibold">[REPLY]</span>{" "}
                    <span className="text-zinc-800 dark:text-zinc-300">{pick.debate.rebuttal.rebuttal}</span>
                  </div>
                  <div className="text-zinc-500 ml-4">
                    score → {pick.debate.rebuttal.revised_score}/100 · conceded: {pick.debate.rebuttal.concede ? "yes" : "no"}
                  </div>
                </div>
              )}

              {/* ── JUDGE ── */}
              {pick.debate?.arbiter_summary && (
                <div>
                  <div className="text-zinc-500 mb-1">── Judge ──</div>
                  <div>
                    <span className="text-emerald-400 font-semibold">[JUDGE]</span>{" "}
                    <span className="text-zinc-800 dark:text-zinc-300">{pick.debate.arbiter_summary}</span>
                  </div>
                  <div className="text-zinc-500 ml-4">
                    final confidence {pick.adjusted_score}/100 · {plainVerdict(pick.verdict)} ·{" "}
                    {pick.approved ? "APPROVED" : "thin lean"}
                    {pick.debate?.flipped && (
                      <span className="text-fuchsia-400"> · REVERSED by critic</span>
                    )}
                  </div>
                </div>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
