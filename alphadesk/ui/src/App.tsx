import { lazy, Suspense, useEffect, useState } from "react"
import { BrowserRouter, Navigate, Route, Routes, useLocation } from "react-router-dom"
import { api, type LivePick, type SymbolTimeline, type EarningsRow } from "@/lib/api"
import { useTheme } from "@/lib/theme"
import { Header } from "@/components/Header"
import { Nav } from "@/components/Nav"
import { Moon, Monitor, Sun } from "lucide-react"

// Lazy routes — each page is its own chunk, so it loads like a real page.
const DashboardPage = lazy(() => import("@/pages/DashboardPage"))
const ScreenerPage = lazy(() => import("@/pages/ScreenerPage"))
const FilingsPage = lazy(() => import("@/pages/FilingsPage"))
const ResearchPage = lazy(() => import("@/pages/ResearchPage"))
const TradePage = lazy(() => import("@/pages/TradePage"))
const PerformancePage = lazy(() => import("@/pages/PerformancePage"))
const LivePage = lazy(() => import("@/pages/LivePage"))
const HistoryPage = lazy(() => import("@/pages/HistoryPage"))
const EarningsPage = lazy(() => import("@/pages/EarningsPage"))
const SystemPage = lazy(() => import("@/pages/SystemPage"))

const TITLES: Record<string, string> = {
  "/dashboard": "Dashboard · AlphaDesk",
  "/screener": "Screener · AlphaDesk",
  "/filings": "Filings · AlphaDesk",
  "/research": "Research · AlphaDesk",
  "/trade": "Trade · AlphaDesk",
  "/performance": "Performance · AlphaDesk",
  "/live": "Live Positions · AlphaDesk",
  "/history": "History · AlphaDesk",
  "/earnings": "Earnings · AlphaDesk",
  "/system": "System Health · AlphaDesk",
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
    const load = () => api.timelines().then(d => { if (alive) { setSymbols(d.symbols); setHistoryLoaded(true) } }).catch(() => {})
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
      {/* Single 30px header bar. No max-width container anywhere in the shell:
          a data-dense terminal uses the full width of the display, and a
          centred 1200px column would waste half a monitor. */}
      <header className="z-30 flex h-[30px] shrink-0 items-stretch border-b border-border bg-panel-header">
        <div className="flex shrink-0 items-center gap-1.5 px-2">
          <span className="h-2 w-2 bg-accent" />
          <span className="text-[11px] font-bold tracking-[0.06em]">ALPHADESK</span>
        </div>
        <div className="w-px shrink-0 bg-border" />
        <Nav />
        <div className="min-w-0 flex-1" />
        <Header liveOpenCount={liveOpen} />
        <button
          onClick={toggleTheme}
          aria-label="Toggle theme"
          className="flex w-8 shrink-0 items-center justify-center border-l border-border text-muted-foreground hover:bg-muted hover:text-foreground"
        >
          {theme === "dark" ? <Moon className="h-3 w-3" /> : theme === "light" ? <Sun className="h-3 w-3" /> : <Monitor className="h-3 w-3" />}
        </button>
      </header>
      <main className="min-h-0 flex-1 overflow-y-auto">
        <div className="p-1">
          <Suspense fallback={<div className="p-2 text-[11px] text-muted-foreground">loading…</div>}>
            <Routes>
              <Route path="/" element={<Navigate to="/dashboard" replace />} />
              <Route path="/dashboard" element={<DashboardPage />} />
              <Route path="/screener" element={<ScreenerPage />} />
              <Route path="/filings" element={<FilingsPage />} />
              <Route path="/research" element={<ResearchPage />} />
              <Route path="/trade" element={<TradePage />} />
              <Route path="/performance" element={<PerformancePage />} />
              <Route path="/live" element={<LivePage rows={liveRows} market={market} loading={!liveLoaded} />} />
              <Route path="/history" element={<HistoryPage symbols={symbols} loading={!historyLoaded} />} />
              <Route path="/earnings" element={<EarningsPage earnings={earnings} />} />
              <Route path="/system" element={<SystemPage />} />
              {/* /open merged into Live (session filter) — MarketPage.tsx deleted 2026-08-17,
                  it was a near-duplicate of Live+History filtered to one session, a leftover
                  of the old multi-session bot loop. */}
              <Route path="/open" element={<Navigate to="/live" replace />} />
              <Route path="*" element={<Navigate to="/dashboard" replace />} />
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
