import { lazy, Suspense, useEffect, useState } from "react"
import { BrowserRouter, Navigate, Route, Routes, useLocation } from "react-router-dom"
import { api, type LivePick, type SymbolTimeline, type Stats, type EarningsRow } from "@/lib/api"
import { useTheme } from "@/lib/theme"
import { Header } from "@/components/Header"
import { Nav } from "@/components/Nav"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Separator } from "@/components/ui/separator"
import { Moon, Monitor, Sun } from "lucide-react"

// Lazy routes — each page is its own chunk, so it loads like a real page.
const LivePage = lazy(() => import("@/pages/LivePage"))
const HistoryPage = lazy(() => import("@/pages/HistoryPage"))
const EarningsPage = lazy(() => import("@/pages/EarningsPage"))
const MarketPage = lazy(() => import("@/pages/MarketPage"))

const TITLES: Record<string, string> = {
  "/live": "Live Positions · AlphaDesk",
  "/history": "History · AlphaDesk",
  "/pre": "Pre-Market · AlphaDesk",
  "/open": "Open Market · AlphaDesk",
  "/after": "After Hours · AlphaDesk",
  "/earnings": "Earnings · AlphaDesk",
}

function Shell() {
  const { pathname } = useLocation()
  const [theme, toggleTheme] = useTheme()

  // All data lives here — survives page navigation (real multipage URLs, SPA data).
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

  // History + stats: 60s poll
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

  // Earnings: lazy — fetch once the Earnings page is first visited, then 5min poll
  const [earningsVisited, setEarningsVisited] = useState(false)
  useEffect(() => { if (pathname === "/earnings") setEarningsVisited(true) }, [pathname])
  useEffect(() => {
    if (!earningsVisited) return
    const load = () => api.earnings().then(d => setEarnings(d || { upcoming: [], reported: [] })).catch(() => setEarnings(prev => prev ?? { upcoming: [], reported: [] }))
    load()
    const t = setInterval(load, 300_000)
    return () => clearInterval(t)
  }, [earningsVisited])

  // Per-page document title
  useEffect(() => { document.title = TITLES[pathname] ?? "AlphaDesk" }, [pathname])

  return (
    <div className="flex h-screen flex-col overflow-hidden bg-background text-foreground">
      <header className="z-30 shrink-0 border-b border-border bg-background/85 backdrop-blur">
        <div className="mx-auto flex max-w-[1440px] items-center gap-3 px-5 py-2.5">
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
          <Nav />
          <Separator orientation="vertical" className="mx-1 h-8" />
          <div className="flex-1" />
          <Header liveOpenCount={liveOpen} />
          <Button variant="ghost" size="icon" onClick={toggleTheme} aria-label="Toggle theme" className="h-8 w-8 shrink-0">
            {theme === "dark" ? <Moon className="h-4 w-4" /> : theme === "light" ? <Sun className="h-4 w-4" /> : <Monitor className="h-4 w-4" />}
          </Button>
        </div>
      </header>
      <main className="no-scrollbar min-h-0 flex-1 overflow-y-auto">
        <div className="mx-auto max-w-[1200px] space-y-4 px-5 py-5">
          <Suspense fallback={<div className="text-sm text-muted-foreground">Loading…</div>}>
            <Routes>
              <Route path="/" element={<Navigate to="/live" replace />} />
              <Route path="/live" element={<LivePage rows={liveRows} market={market} loading={!liveLoaded} />} />
              <Route path="/history" element={<HistoryPage symbols={symbols} stats={stats} loading={!historyLoaded} />} />
              <Route path="/pre" element={<MarketPage session="PRE" liveRows={liveRows} symbols={symbols} loading={!historyLoaded} />} />
              <Route path="/open" element={<MarketPage session="OPEN" liveRows={liveRows} symbols={symbols} loading={!historyLoaded} />} />
              <Route path="/after" element={<MarketPage session="AFTER" liveRows={liveRows} symbols={symbols} loading={!historyLoaded} />} />
              <Route path="/earnings" element={<EarningsPage earnings={earnings} />} />
              <Route path="*" element={<Navigate to="/live" replace />} />
            </Routes>
          </Suspense>
        </div>
      </main>
    </div>
  )
}

export default function App() {
  return (
    <BrowserRouter>
      <Shell />
    </BrowserRouter>
  )
}
