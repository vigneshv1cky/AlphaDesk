import type { FunnelWindow, TokenRow } from "@/lib/api"

function ScanLog({ windows }: { windows: FunnelWindow[] }) {
  return (
    <div className="rounded-lg border border-border bg-card p-4">
      <div className="mb-2 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
        What it looked at
      </div>
      {windows.length === 0 && (
        <p className="text-sm text-muted-foreground">No scans recorded yet.</p>
      )}
      <div className="space-y-1">
        {windows.map((w) => {
          let skips: { symbol: string; reason: string }[] = []
          try {
            skips = JSON.parse(w.skip_reasons ?? "[]")
          } catch {
            /* ignore */
          }
          return (
            <details key={w.id} className="rounded-md border border-border/60 open:bg-muted/30">
              <summary className="cursor-pointer list-none px-3 py-2 text-sm">
                <span className="text-muted-foreground">
                  {w.window_ts.slice(5, 16).replace("T", " ")}
                </span>{" "}
                — <b>{w.picked} looked into</b> of {w.candidates}, {w.skipped} skipped
              </summary>
              <ul className="space-y-1 px-4 pb-3 pt-1 text-sm text-muted-foreground">
                {skips.map((s, i) => (
                  <li key={i}>
                    <b className="text-foreground">{s.symbol}</b>: {s.reason}
                  </li>
                ))}
                {skips.length === 0 && <li>no skips recorded</li>}
              </ul>
            </details>
          )
        })}
      </div>
    </div>
  )
}

function TokenTable({ tokens }: { tokens: TokenRow[] }) {
  const rows = [...tokens].sort(
    (a, b) => b.input_tok + b.output_tok - (a.input_tok + a.output_tok),
  )
  const total = rows.reduce((s, t) => s + t.input_tok + t.output_tok, 0)
  return (
    <div className="rounded-lg border border-border bg-card p-4">
      <div className="mb-3 flex items-baseline justify-between">
        <div className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
          AI usage today
        </div>
        <div className="text-xs tabular-nums text-muted-foreground">
          {Math.round(total / 1000)}k tokens
        </div>
      </div>
      {rows.length === 0 ? (
        <p className="text-sm text-muted-foreground">No calls yet today.</p>
      ) : (
        <div className="space-y-2.5">
          {rows.slice(0, 12).map((t) => {
            const tot = t.input_tok + t.output_tok
            const pct = total > 0 ? (tot / total) * 100 : 0
            return (
              <div key={t.role + t.model} className="text-sm">
                <div className="flex items-baseline justify-between">
                  <span className="font-medium">
                    {t.role}{" "}
                    <span className="text-xs font-normal text-muted-foreground">{t.model}</span>
                  </span>
                  <span className="text-xs tabular-nums text-muted-foreground">
                    {Math.round(tot / 1000)}k · {t.calls} calls
                  </span>
                </div>
                <div className="mt-1 h-1.5 overflow-hidden rounded-full bg-muted">
                  <div className="h-full rounded-full bg-indigo-500" style={{ width: `${pct}%` }} />
                </div>
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}

export function Activity({
  funnel,
  tokens,
}: {
  funnel?: { paused: string | null; windows: FunnelWindow[] }
  tokens: TokenRow[]
}) {
  return (
    <div className="space-y-3">
      <ScanLog windows={funnel?.windows ?? []} />
      <TokenTable tokens={tokens} />
    </div>
  )
}
