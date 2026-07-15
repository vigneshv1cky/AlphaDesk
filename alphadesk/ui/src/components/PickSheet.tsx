import { useEffect, useState } from "react"
import { api, exitDate, fmtAlpha, type Pick } from "@/lib/api"
import { Badge } from "@/components/ui/badge"
import { Card, CardContent } from "@/components/ui/card"
import { ScrollArea } from "@/components/ui/scroll-area"
import { Separator } from "@/components/ui/separator"
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet"
import { Skeleton } from "@/components/ui/skeleton"
import { ArrowDown, ArrowUp } from "lucide-react"

const ROLE_STYLES: Record<string, string> = {
  triage: "border-l-yellow-500",
  brief: "border-l-zinc-500",
  analyst: "border-l-blue-500",
  skeptic: "border-l-red-500",
  arbiter: "border-l-green-500",
  flag: "border-l-orange-500",
  solo: "border-l-purple-500",
}

function Bubble({
  role,
  who,
  children,
}: {
  role: keyof typeof ROLE_STYLES
  who: string
  children: React.ReactNode
}) {
  return (
    <div className={`rounded-md border border-l-4 ${ROLE_STYLES[role]} bg-card p-3 text-sm`}>
      <div className="mb-1.5 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
        {who}
      </div>
      {children}
    </div>
  )
}

function TheCall({ pick }: { pick: Pick }) {
  const long = pick.direction === "LONG"
  return (
    <Card className="border-2">
      <CardContent className="space-y-2 pt-4">
        <div className="flex items-center gap-2 text-lg font-bold">
          {long ? (
            <ArrowUp className="h-5 w-5 text-green-500" />
          ) : (
            <ArrowDown className="h-5 w-5 text-red-500" />
          )}
          <span className={long ? "text-green-500" : "text-red-500"}>{pick.direction}</span>
          <span>{pick.symbol}</span>
          <span className="text-sm font-normal text-muted-foreground">
            hold {pick.horizon_days} trading days (≈ until{" "}
            {exitDate(pick.ts, pick.session, pick.horizon_days)})
          </span>
        </div>
        <div className="text-sm text-muted-foreground">
          entry {pick.entry_price ? `$${pick.entry_price}` : "next market open"} · conviction{" "}
          {Math.round(pick.adjusted_score ?? pick.score)}/100 · confidence{" "}
          {Math.round(pick.confidence)}/100
        </div>
        <div className="text-sm">
          {pick.approved ? (
            <Badge className="bg-green-600">ON THE BOOK</Badge>
          ) : (
            <Badge variant="destructive">REJECTED — recorded as counterfactual</Badge>
          )}{" "}
          {pick.alpha_net !== null && (
            <Badge variant="outline" className={pick.alpha_net > 0 ? "text-green-500" : "text-red-500"}>
              net alpha {fmtAlpha(pick.alpha_net)}
            </Badge>
          )}
        </div>
      </CardContent>
    </Card>
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

  const tags = pick?.model_tags ?? {}

  return (
    <Sheet open={pickId !== null} onOpenChange={(open) => !open && onClose()}>
      <SheetContent className="w-full sm:max-w-2xl">
        {!pick ? (
          <div className="space-y-3 p-4">
            <Skeleton className="h-24 w-full" />
            <Skeleton className="h-16 w-full" />
            <Skeleton className="h-16 w-full" />
          </div>
        ) : (
          <>
            <SheetHeader className="pb-0">
              <SheetTitle>
                #{pick.id} · {pick.symbol}
              </SheetTitle>
              <SheetDescription className="flex flex-wrap gap-1.5">
                <Badge variant="secondary">{pick.arm}</Badge>
                {pick.edge && <Badge variant="secondary">{pick.edge}</Badge>}
                <Badge variant="secondary">{pick.trigger_src}</Badge>
                <Badge variant="secondary">session {pick.session}</Badge>
                <Badge variant="secondary">{pick.ts.slice(0, 16).replace("T", " ")} UTC</Badge>
              </SheetDescription>
            </SheetHeader>
            <ScrollArea className="h-[calc(100vh-8rem)] px-4">
              <div className="space-y-3 pb-8">
                <TheCall pick={pick} />
                <div className="text-center text-xs text-muted-foreground">
                  score {Math.round(pick.score)} → {pick.adjusted_score ?? "—"} · verdict{" "}
                  {pick.verdict ?? "—"}
                </div>
                <Separator />

                {pick.triage_reason && (
                  <Bubble role="triage" who="Triage — why this deserved the committee">
                    {pick.triage_reason}
                  </Bubble>
                )}

                {(pick.briefs ?? []).map((b, i) => (
                  <Bubble key={i} role="brief" who={`${b.kind} brief (subagent)`}>
                    <p>{b.summary}</p>
                    {b.key_facts && b.key_facts.length > 0 && (
                      <ul className="mt-1.5 list-disc pl-4 text-muted-foreground">
                        {b.key_facts.map((f, j) => (
                          <li key={j}>{typeof f === "string" ? f : f.fact}</li>
                        ))}
                      </ul>
                    )}
                  </Bubble>
                ))}

                {pick.thesis && (
                  <Bubble role="analyst" who={`Analyst (${tags.analyst ?? "?"}) — thesis`}>
                    <p>{pick.thesis}</p>
                    <p className="mt-1.5 text-muted-foreground">
                      score {Math.round(pick.score)} · horizon {pick.horizon_days}d
                    </p>
                  </Bubble>
                )}

                {(pick.debate?.concerns ?? []).map((c, i) => (
                  <Bubble key={i} role="skeptic" who={`Skeptic (${tags.skeptic ?? "?"}) — attack ${i + 1}`}>
                    <p className="font-medium">{c.claim}</p>
                    <p className="mt-1 text-muted-foreground">{c.evidence}</p>
                  </Bubble>
                ))}

                {(pick.debate?.fact_flags ?? []).map((f, i) => (
                  <Bubble key={i} role="flag" who="Fact-check (code)">
                    {f}
                  </Bubble>
                ))}

                {pick.debate?.rebuttal && (
                  <Bubble role="analyst" who="Analyst — rebuttal">
                    <p>{pick.debate.rebuttal.rebuttal}</p>
                    <p className="mt-1.5 text-muted-foreground">
                      revised score {pick.debate.rebuttal.revised_score} · conceded:{" "}
                      {String(pick.debate.rebuttal.concede)}
                    </p>
                  </Bubble>
                )}

                {pick.debate?.arbiter_summary && (
                  <Bubble role="arbiter" who={`Arbiter (${tags.arbiter ?? "?"}) — verdict`}>
                    <p>{pick.debate.arbiter_summary}</p>
                    <p className="mt-1.5 text-muted-foreground">
                      adjusted {pick.adjusted_score} · confidence {Math.round(pick.confidence)} ·{" "}
                      {pick.verdict} · approved: {String(Boolean(pick.approved))}
                    </p>
                  </Bubble>
                )}

                {pick.arm === "SOLO" && (
                  <Bubble role="solo" who={`Solo analyst (${tags.solo ?? "?"}) — control arm`}>
                    Worked the same evidence blind to the committee.
                  </Bubble>
                )}
              </div>
            </ScrollArea>
          </>
        )}
      </SheetContent>
    </Sheet>
  )
}
