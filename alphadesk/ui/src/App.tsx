import { lazy, Suspense, useEffect, useState } from "react"
import { BrowserRouter, Navigate, Route, Routes, useLocation } from "react-router-dom"
import { useTheme } from "@/lib/theme"
import { Header } from "@/components/Header"
import { Sidebar } from "@/components/Sidebar"
import { TickerTape } from "@/components/TickerTape"
import { AiRail } from "@/components/AiRail"
import { Moon, Monitor, Sun } from "lucide-react"

// Lazy routes — each page is its own chunk, so it loads like a real page.
const DashboardPage = lazy(() => import("@/pages/DashboardPage"))
const NewsPage = lazy(() => import("@/pages/NewsPage"))
const AnalysisPage = lazy(() => import("@/pages/AnalysisPage"))
const PortfolioPage = lazy(() => import("@/pages/PortfolioPage"))
const EarningsPage = lazy(() => import("@/pages/EarningsPage"))
const SystemPage = lazy(() => import("@/pages/SystemPage"))

const TITLES: Record<string, string> = {
  "/markets": "Markets · AlphaDesk",
  "/analysis": "Analysis · AlphaDesk",
  "/news": "News · AlphaDesk",
  "/portfolio": "My Portfolio · AlphaDesk",
  "/earnings": "Earnings · AlphaDesk",
  "/system": "System Health · AlphaDesk",
}

/** ET clock, pinned in the header. Timestamps across the terminal are ET, so
 * the clock anchors them; showing the viewer's local time would make every
 * other time on screen ambiguous.
 *
 * No session badge. It said CLOSED for two-thirds of the day while the tape
 * above it carried crypto that never stops and futures that barely do — a
 * status line contradicted by the data beside it is worse than none. */
function MarketClock() {
  const [now, setNow] = useState(() => new Date())
  useEffect(() => {
    const t = setInterval(() => setNow(new Date()), 30_000)
    return () => clearInterval(t)
  }, [])
  const time = now.toLocaleTimeString("en-US", {
    timeZone: "America/New_York", hour: "numeric", minute: "2-digit",
  })
  const day = now.toLocaleDateString("en-US", {
    timeZone: "America/New_York", weekday: "short", month: "short", day: "numeric",
  })
  return (
    <div className="flex shrink-0 items-center gap-2 px-2">
      <span className="num text-[14px]">{time} ET</span>
      <span className="text-[12px] text-muted-foreground">· {day}</span>
    </div>
  )
}

/** A redirect that keeps ?symbol= / ?accession=. A bare <Navigate> would drop
 * the query, so an old /chart?symbol=NVDA link would land on Analysis showing
 * the default company instead of the one that was linked. */
function RedirectKeepingQuery({ to }: { to: string }) {
  const { search } = useLocation()
  return <Navigate to={`${to}${search}`} replace />
}

function Shell() {
  const { pathname } = useLocation()
  const [theme, toggleTheme] = useTheme()

  // The Earnings page fetches its own week now, so nothing is threaded through
  // here and the endpoint is only hit once that route mounts.

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
          <span className="text-[14px] font-bold tracking-[0.06em]">ALPHADESK</span>
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
        <div>
          <Suspense fallback={<div className="p-2 text-[14px] text-muted-foreground">loading…</div>}>
            <Routes>
              <Route path="/" element={<Navigate to="/markets" replace />} />
              <Route path="/markets" element={<DashboardPage />} />
              <Route path="/analysis" element={<AnalysisPage />} />
              <Route path="/news" element={<NewsPage />} />
              <Route path="/portfolio" element={<PortfolioPage />} />
              <Route path="/earnings" element={<EarningsPage />} />
              <Route path="/system" element={<SystemPage />} />
              {/* Old paths, kept as redirects so links and bookmarks still
                  land somewhere. /chart and /filings merged into Analysis and
                  carry their ?symbol= across; /research was only ever an ask
                  form, and the rail does that from every route now. */}
              <Route path="/dashboard" element={<Navigate to="/markets" replace />} />
              <Route path="/screener" element={<Navigate to="/news" replace />} />
              <Route path="/chart" element={<RedirectKeepingQuery to="/analysis" />} />
              <Route path="/filings" element={<RedirectKeepingQuery to="/analysis" />} />
              <Route path="/trade" element={<RedirectKeepingQuery to="/analysis" />} />
              <Route path="/research" element={<RedirectKeepingQuery to="/analysis" />} />
              <Route path="*" element={<Navigate to="/markets" replace />} />
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
