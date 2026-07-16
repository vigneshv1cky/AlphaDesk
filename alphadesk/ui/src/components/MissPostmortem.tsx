import { useState } from "react"
import { api, type MissResult } from "@/lib/api"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Loader2, SearchX } from "lucide-react"

const FIX_STYLE: Record<string, string> = {
  DATA: "bg-blue-600",
  PROMPT: "bg-amber-600",
  BUG: "bg-red-600",
  NONE: "bg-zinc-600",
}

const FIX_LABEL: Record<string, string> = {
  DATA: "Data / coverage gap",
  PROMPT: "Judgment — prompt fix",
  BUG: "Mechanical bug",
  NONE: "Correctly skipped",
}

export function MissPostmortem() {
  const [symbol, setSymbol] = useState("")
  const [note, setNote] = useState("")
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState("")
  const [res, setRes] = useState<MissResult | null>(null)

  async function run() {
    if (!symbol.trim()) return
    setBusy(true)
    setErr("")
    setRes(null)
    try {
      setRes(await api.miss(symbol.trim().toUpperCase(), note))
    } catch (e) {
      setErr(String(e))
    } finally {
      setBusy(false)
    }
  }

  const skips = res?.evidence.triage_skips ?? []
  const rejections = (res?.evidence.rejections ?? []) as Record<string, unknown>[]

  return (
    <Card>
      <CardHeader className="pb-3">
        <CardTitle className="flex items-center gap-2 text-lg">
          <SearchX className="h-5 w-5 text-muted-foreground" />
          Why did we miss this?
        </CardTitle>
        <p className="text-sm text-muted-foreground">
          Name a stock the desk should have caught — it traces our own logs to find where
          it fell out and whether the miss is worth fixing.
        </p>
      </CardHeader>
      <CardContent className="space-y-3">
        <div className="flex flex-wrap gap-2">
          <input
            value={symbol}
            onChange={(e) => setSymbol(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && run()}
            placeholder="TICKER"
            className="w-28 rounded-md border bg-background px-3 py-2 text-sm font-mono uppercase outline-none focus:ring-1 focus:ring-ring"
          />
          <input
            value={note}
            onChange={(e) => setNote(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && run()}
            placeholder="what was the opportunity? (e.g. ran 15% on datacenter capex ~Jul 8)"
            className="min-w-[16rem] flex-1 rounded-md border bg-background px-3 py-2 text-sm outline-none focus:ring-1 focus:ring-ring"
          />
          <Button onClick={run} disabled={busy || !symbol.trim()}>
            {busy ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : null}
            {busy ? "Tracing…" : "Diagnose"}
          </Button>
        </div>

        {err && <p className="text-sm text-red-500">{err}</p>}

        {res && (
          <div className="space-y-3 rounded-md border bg-muted/30 p-3 text-sm">
            <div className="flex items-center gap-2">
              <span className="font-bold">{res.symbol}</span>
              <Badge className={FIX_STYLE[res.fix_type] ?? "bg-zinc-600"}>
                {FIX_LABEL[res.fix_type] ?? res.fix_type}
              </Badge>
              <span className="text-muted-foreground">{res.stage_label}</span>
            </div>

            <div>
              <div className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                What happened
              </div>
              <p>{res.what_happened}</p>
            </div>

            <div>
              <div className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                Diagnosis
              </div>
              <p>{res.diagnosis}</p>
            </div>

            <div>
              <div className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                Suggested fix
              </div>
              <p>{res.suggested_fix}</p>
            </div>

            {res.hindsight_risk && res.hindsight_risk !== "n/a" && (
              <div className="rounded-md border-l-4 border-l-amber-500 bg-amber-950/20 p-2">
                <div className="text-xs font-semibold uppercase tracking-wider text-amber-500">
                  Hindsight check
                </div>
                <p className="text-muted-foreground">{res.hindsight_risk}</p>
              </div>
            )}

            {(skips.length > 0 || rejections.length > 0) && (
              <div className="border-t pt-2">
                <div className="mb-1 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                  Evidence trail
                </div>
                <ul className="list-disc space-y-1 pl-5 text-muted-foreground">
                  {rejections.map((r, i) => (
                    <li key={`r${i}`}>
                      <span className="text-foreground">Debated &amp; rejected</span> ({String(r.when)}):{" "}
                      {String(r.arbiter_summary ?? r.thesis ?? "")}
                    </li>
                  ))}
                  {skips.map((s, i) => (
                    <li key={`s${i}`}>
                      <span className="text-foreground">Triage skip</span> (
                      {s.window_ts.slice(5, 10)}): {s.reason}
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </div>
        )}
      </CardContent>
    </Card>
  )
}
