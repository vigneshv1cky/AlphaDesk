import { useEffect, useState } from "react"
import { useSearchParams } from "react-router-dom"
import { api, type FilingRow } from "@/lib/api"
import { Badge, Empty, Widget } from "@/components/terminal"

/** SEC filings for one symbol, straight from EDGAR — free, no vendor, no API
 * key. Pick a filing, then ask it something in the AI rail: every answer is
 * backed only by verbatim quotes checked against the actual document text
 * server-side, so the model cannot fabricate a citation that does not
 * literally appear in the filing. */
export function SymbolFilings({ symbol: requested }: { symbol: string }) {
  const [params, setParams] = useSearchParams()
  const [symbol, setSymbol] = useState("")
  const [filings, setFilings] = useState<FilingRow[] | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const [selected, setSelected] = useState<FilingRow | null>(null)

  const load = (sym: string) => {
    if (!sym) return
    setLoading(true)
    setErr(null)
    setSelected(null)
    api.filings(sym)
      .then(d => { setFilings(d.filings); setSymbol(d.symbol) })
      .catch(e => { setFilings(null); setErr(String(e.message ?? e)) })
      .finally(() => setLoading(false))
  }

  // eslint-disable-next-line react-hooks/exhaustive-deps -- follow the symbol
  // Analysis is scoped to, not this component's own identity
  useEffect(() => { if (requested) load(requested) }, [requested])

  return (
      <Widget
        span={12}
        title="Filings"
        subtitle="SEC EDGAR direct — every answer is a verbatim quote from the document, never a paraphrase passed off as fact"
      >
      {(loading || err) && (
        <div className="border-b border-border p-1 text-[14px]">
          {loading && <span className="text-muted-foreground">loading…</span>}
          {err && <span className="text-loss">{err}</span>}
        </div>
      )}

      {filings && (
        <div className="grid lg:grid-cols-[220px_1fr]">
          <div className="border-r border-border">
            {filings.length === 0 ? (
              <Empty>No filings found for {symbol}.</Empty>
            ) : filings.map(f => (
              <button
                key={f.accession}
                onClick={() => {
                  setSelected(f)
                  // Mirror the selection into the URL: that is how the AI rail
                  // learns which document you are looking at.
                  const next = new URLSearchParams(params)
                  next.set("symbol", f.symbol)
                  next.set("accession", f.accession)
                  setParams(next, { replace: true })
                }}
                className={`flex w-full items-center justify-between gap-2 border-b border-grid-line px-2 py-1 text-left transition-colors ${
                  selected?.accession === f.accession
                    ? "bg-accent/15 text-foreground"
                    : "hover:bg-muted"
                }`}
              >
                <Badge variant={selected?.accession === f.accession ? "default" : "secondary"}>{f.form}</Badge>
                <span className="num text-[12px] text-muted-foreground">{f.filing_date}</span>
              </button>
            ))}
          </div>

          <div className="min-w-0">
            {selected ? (
              <FilingReader filing={selected} />
            ) : (
              <Empty>Pick a filing on the left to read it or ask it something.</Empty>
            )}
          </div>
        </div>
      )}
      </Widget>
  )
}

function FilingReader({ filing }: { filing: FilingRow }) {

  return (
    <div className="space-y-1">
      <div className="flex items-center justify-between">
        <div>
          <span className="text-sm font-semibold">{filing.symbol} {filing.form}</span>
          <span className="ml-2 text-xs text-muted-foreground">filed {filing.filing_date}</span>
        </div>
        <a href={filing.url} target="_blank" rel="noreferrer"
          className="text-xs text-muted-foreground underline decoration-dotted hover:text-foreground">
          Original on sec.gov ↗
        </a>
      </div>

      <div className="px-2 pb-1 text-[12px] text-muted-foreground">
        Ask this filing anything in the AI panel on the right — answers there are
        verbatim quotes verified against this document.
      </div>
    </div>
  )
}
