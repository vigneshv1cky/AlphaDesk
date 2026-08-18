import { lazy, Suspense, useEffect, useState } from "react"
import { BrowserRouter, Navigate, Route, Routes, useLocation } from "react-router-dom"
import { type EarningsRow } from "@/lib/api"
import { useEarnings, useSystem } from "@/lib/queries"
import { useTheme } from "@/lib/theme"
import { Header } from "@/components/Header"
import { Sidebar } from "@/components/Sidebar"
import { TickerTape } from "@/components/TickerTape"
import { AiRail } from "@/components/AiRail"
import { Moon, Monitor, Sun } from "lucide-react"

// Lazy routes — each page is its own chunk, so it loads like a real page.
const DashboardPage = lazy(() => import("@/pages/DashboardPage"))
const ScreenerPage = lazy(() => import("@/pages/ScreenerPage"))
const FilingsPage = lazy(() => import("@/pages/FilingsPage"))
const ChartPage = lazy(() => import("@/pages/ChartPage"))
const EarningsPage = lazy(() => import("@/pages/EarningsPage"))
const SystemPage = lazy(() => import("@/pages/SystemPage"))

const TITLES: Record<string, string> = {
  "/dashboard": "Dashboard · AlphaDesk",
  "/screener": "Screener · AlphaDesk",
  "/filings": "Filings · AlphaDesk",
  "/chart": "Chart · AlphaDesk",
  "/earnings": "Earnings · AlphaDesk",
  "/system": "System Health · AlphaDesk",
}

/** ET clock + market session, pinned in the header. The market runs on US
 * Eastern; showing the viewer's local time would make every other timestamp on
 * the terminal ambiguous. */
function MarketClock() {
  const [now, setNow] = useState(() => new Date())
  useEffect(() => {
    const t = setInterval(() => setNow(new Date()), 30_000)
    return () => clearInterval(t)
  }, [])
  const { data } = useSystem()
  const time = now.toLocaleTimeString("en-US", {
    timeZone: "America/New_York", hour: "numeric", minute: "2-digit",
  })
  const day = now.toLocaleDateString("en-US", {
    timeZone: "America/New_York", weekday: "short", month: "short", day: "numeric",
  })
  const sess = data?.market ?? ""
  const label = sess === "OPEN" ? "Open" : sess === "PRE" ? "Pre-Mkt"
    : sess === "AFTER" ? "After-Hrs" : sess === "CLOSED" ? "Closed" : ""
  return (
    <div className="flex shrink-0 items-center gap-2 px-2">
      <span className="num text-[11px]">{time} ET</span>
      <span className="text-[10px] text-muted-foreground">· {day}</span>
      {label && (
        <span className={`border px-1 text-[9px] font-semibold uppercase tracking-[0.06em] ${
          sess === "OPEN" ? "border-gain/40 text-gain" : "border-border text-muted-foreground"
        }`}>
          {label}
        </span>
      )}
    </div>
  )
}

function Shell() {
  const { pathname } = useLocation()
  const [theme, toggleTheme] = useTheme()

  // Earnings stays lazy — it is the most expensive endpoint, so it is not
  // fetched until the Earnings page has been visited at least once.
  const [earningsVisited, setEarningsVisited] = useState(false)
  useEffect(() => { if (pathname === "/earnings") setEarningsVisited(true) }, [pathname])
  const earningsQuery = useEarnings(earningsVisited)
  const earnings: { upcoming: EarningsRow[]; reported: EarningsRow[] } | null =
    earningsVisited ? (earningsQuery.data ?? { upcoming: [], reported: [] }) : null

  // Per-page document title
  useEffect(() => { document.title = TITLES[pathname] ?? "AlphaDesk" }, [pathname])

  return (
    <div className="flex h-screen flex-col overflow-hidden bg-background text-foreground">
      {/* Single 30px header bar. No max-width container anywhere in the shell:
          a data-dense terminal uses the full width of the display, and a
          centred 1200px column would waste half a monitor. */}
      <header className="z-30 flex h-[30px] shrink-0 items-stretch border-b border-border bg-panel-header">
        <div className="flex w-[150px] shrink-0 items-center gap-1.5 border-r border-border px-2">
          <span className="h-2 w-2 bg-accent" />
          <span className="text-[11px] font-bold tracking-[0.06em]">ALPHADESK</span>
        </div>
        <MarketClock />
        <div className="min-w-0 flex-1" />
        <Header />
        <button
          onClick={toggleTheme}
          aria-label="Toggle theme"
          className="flex w-8 shrink-0 items-center justify-center border-l border-border text-muted-foreground hover:bg-muted hover:text-foreground"
        >
          {theme === "dark" ? <Moon className="h-3 w-3" /> : theme === "light" ? <Sun className="h-3 w-3" /> : <Monitor className="h-3 w-3" />}
        </button>
      </header>
      {/* The market strip spans the full width UNDER the header and ABOVE the
          sidebar split — it is context for everything, not for one view. */}
      <TickerTape />
      {/* Sidebar, content and AI rail are three flex siblings. The rail is a
          real column the grid reflows around, not an overlay: on a dense
          terminal an overlay would cover the thing you are asking about. */}
      <div className="flex min-h-0 flex-1">
      <Sidebar />
      <main className="min-h-0 flex-1 overflow-y-auto">
        <div className="p-1">
          <Suspense fallback={<div className="p-2 text-[11px] text-muted-foreground">loading…</div>}>
            <Routes>
              <Route path="/" element={<Navigate to="/dashboard" replace />} />
              <Route path="/dashboard" element={<DashboardPage />} />
              <Route path="/screener" element={<ScreenerPage />} />
              <Route path="/filings" element={<FilingsPage />} />
              {/* /research was nothing but an ask form; the AI rail's Symbol
                  mode is that same call, available from every route. Kept as a
                  redirect so old links and bookmarks still land somewhere. */}
              <Route path="/research" element={<Navigate to="/dashboard" replace />} />
              <Route path="/chart" element={<ChartPage />} />
              <Route path="/trade" element={<Navigate to="/chart" replace />} />
              <Route path="/earnings" element={<EarningsPage earnings={earnings} />} />
              <Route path="/system" element={<SystemPage />} />
              <Route path="*" element={<Navigate to="/dashboard" replace />} />
            </Routes>
          </Suspense>
        </div>
      </main>
      <AiRail />
      </div>
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
