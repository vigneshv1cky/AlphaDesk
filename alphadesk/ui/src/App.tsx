import { useEffect, useState, useCallback } from "react"
import { api, type LivePick, type SymbolTimeline, type Stats, type EarningsRow } from "@/lib/api"
import { useTheme } from "@/lib/theme"
import { Header } from "@/components/Header"
import { LivePositions } from "@/components/LivePositions"
import { History } from "@/components/History"
import { Earnings } from "@/components/Earnings"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Separator } from "@/components/ui/separator"
import { Moon, Monitor, Sun } from "lucide-react"

function readHash(): string {
  const h = window.location.hash.replace("#", "")
  return h && ["live","history","earnings"].includes(h) ? h : "live"
}

export default function App() {
  const [theme, toggleTheme] = useTheme()
  const [tab, setTab] = useState(readHash)

  // All data lives here — survives tab unmounts
  const [liveRows, setLiveRows] = useState<LivePick[]>([])
  const [market, setMarket] = useState("")
  const [liveLoaded, setLiveLoaded] = useState(false)
  const [liveOpen, setLiveOpen] = useState(0)

  const [symbols, setSymbols] = useState<SymbolTimeline[]>([])
  const [stats, setStats] = useState<Stats | null>(null)
  const [historyLoaded, setHistoryLoaded] = useState(false)

  const [earnings, setEarnings] = useState<{ upcoming: EarningsRow[]; reported: EarningsRow[] } | null>(null)

  // Live: 15s poll
  useEffect(() => {
    let alive = true
    const load = () => api.live().then(d => { if (!alive) return; setLiveRows(d.live); setMarket(d.market); setLiveLoaded(true); setLiveOpen(d.live.length) }).catch(() => {})
    load()
    const t = setInterval(load, 15_000)
    return () => { alive = false; clearInterval(t) }
  }, [])

  // History: 60s poll
  useEffect(() => {
    let alive = true
    const load = () => {
      api.timelines().then(d => { if (alive) { setSymbols(d.symbols); setHistoryLoaded(true) } }).catch(() => {})
      api.stats().then(s => { if (alive) setStats(s) }).catch(() => {})
    }
    load()
    const t = setInterval(load, 60_000)
    return () => { alive = false; clearInterval(t) }
  }, [])

  // Earnings: 5min poll, lazy first load
  const [earningsTabVisited, setEarningsTabVisited] = useState(false)
  useEffect(() => {
    if (!earningsTabVisited || earnings) return
    api.earnings().then(d => setEarnings(d || { upcoming: [], reported: [] })).catch(() => setEarnings({ upcoming: [], reported: [] }))
  }, [earningsTabVisited, earnings])

  const onTabChange = useCallback((value: string) => {
    setTab(value)
    window.location.hash = value
    if (value === "earnings") setEarningsTabVisited(true)
  }, [])

  return (
    <div className="flex h-screen flex-col overflow-hidden bg-background text-foreground">
      <header className="z-30 shrink-0 border-b border-border bg-background/85 backdrop-blur">
        <div className="mx-auto flex max-w-[1200px] items-center gap-3 px-5 py-2.5">
          <div className="flex items-center gap-2.5">
            <span className="h-3.5 w-3.5 rotate-45 rounded-[3px] bg-indigo-500" />
            <div className="leading-none">
              <div className="text-sm font-bold tracking-tight">AlphaDesk</div>
              <div className="mt-0.5 text-[10px] text-muted-foreground">Quant engine</div>
            </div>
          </div>
          <Badge className="gap-1 rounded-full bg-emerald-500/10 px-2 py-0.5 text-[11px] font-semibold text-emerald-500">
            <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-emerald-500" />auto
          </Badge>
          <Separator orientation="vertical" className="mx-1 h-8" />
          <Header liveOpenCount={liveOpen} />
          <Separator orientation="vertical" className="mx-1 h-8" />
          <Button variant="ghost" size="icon" onClick={toggleTheme} aria-label="Toggle theme" className="h-8 w-8 shrink-0">
            {theme === "dark" ? <Moon className="h-4 w-4" /> : theme === "light" ? <Sun className="h-4 w-4" /> : <Monitor className="h-4 w-4" />}
          </Button>
        </div>
      </header>
      <main className="no-scrollbar min-h-0 flex-1 overflow-y-auto">
        <div className="mx-auto max-w-[1200px] space-y-4 px-5 py-5">
          <Tabs value={tab} onValueChange={onTabChange}>
            <TabsList className="h-9">
              <TabsTrigger value="live" className="px-4 text-sm data-[state=active]:bg-indigo-600 data-[state=active]:text-white">Live</TabsTrigger>
              <TabsTrigger value="history" className="px-4 text-sm data-[state=active]:bg-indigo-600 data-[state=active]:text-white">History</TabsTrigger>
              <TabsTrigger value="earnings" className="px-4 text-sm data-[state=active]:bg-indigo-600 data-[state=active]:text-white">Earnings</TabsTrigger>
            </TabsList>
            <TabsContent value="live"><LivePositions rows={liveRows} market={market} loading={!liveLoaded} /></TabsContent>
            <TabsContent value="history"><History symbols={symbols} stats={stats} loading={!historyLoaded} /></TabsContent>
            <TabsContent value="earnings"><Earnings earnings={earnings} /></TabsContent>
          </Tabs>
        </div>
      </main>
    </div>
  )
}
