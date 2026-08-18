import { NavLink } from "react-router-dom"
import {
  Activity, BarChart3, CalendarDays, FileText, LineChart, Newspaper, Wrench,
} from "lucide-react"

/** Left rail — sectioned, icon-led, with an identity card pinned to the
 * bottom. Mirrors AlphaSpace's shape: a VIEWS group of surfaces, a THEMES
 * group of saved symbol sets, and account chrome underneath.
 *
 * Vertical rather than top tabs because a terminal's horizontal band is spoken
 * for by the ticker strip, and a list grows without wrapping.
 */
const VIEWS = [
  { to: "/dashboard", label: "Markets", Icon: Activity },
  { to: "/screener", label: "Screener", Icon: BarChart3 },
  { to: "/chart", label: "Charts", Icon: LineChart },
  { to: "/earnings", label: "Earnings Hub", Icon: CalendarDays },
  { to: "/filings", label: "Filings", Icon: FileText },
  { to: "/system", label: "Health", Icon: Wrench },
]

/** Themes are pre-set symbol groups. They deep-link into the screener filter
 * rather than being a separate surface — the same list, narrowed. */
const THEMES = [
  { q: "NVDA", label: "AI & Semis", Icon: Newspaper },
  { q: "AAPL", label: "Mega Cap", Icon: Newspaper },
  { q: "XL", label: "Sector ETFs", Icon: Newspaper },
]

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="py-1.5">
      <div className="px-3 pb-1 text-[10px] font-semibold uppercase tracking-[0.1em] text-muted-foreground/70">
        {title}
      </div>
      {children}
    </div>
  )
}

export function Sidebar() {
  return (
    <nav className="flex w-[190px] shrink-0 flex-col border-r border-border bg-panel">
      <Section title="Views">
        {VIEWS.map(({ to, label, Icon }) => (
          <NavLink
            key={to}
            to={to}
            className={({ isActive }) =>
              `mx-2 flex items-center gap-2.5 rounded-md px-2 py-[7px] text-[12px] transition-colors ${
                isActive
                  ? "bg-muted font-medium text-foreground"
                  : "text-muted-foreground hover:bg-muted/60 hover:text-foreground"
              }`
            }
          >
            {({ isActive }) => (
              <>
                <Icon className={`h-[15px] w-[15px] shrink-0 ${isActive ? "text-accent" : ""}`} />
                {label}
              </>
            )}
          </NavLink>
        ))}
      </Section>

      <Section title="Themes">
        {THEMES.map(({ q, label, Icon }) => (
          <NavLink
            key={label}
            to={`/screener?q=${encodeURIComponent(q)}`}
            className="mx-2 flex items-center gap-2.5 rounded-md px-2 py-[7px] text-[12px] text-muted-foreground transition-colors hover:bg-muted/60 hover:text-foreground"
          >
            <Icon className="h-[15px] w-[15px] shrink-0" />
            {label}
          </NavLink>
        ))}
      </Section>

      <div className="flex-1" />

      <div className="border-t border-border p-3">
        <div className="text-[10px] leading-relaxed text-muted-foreground/60">
          Consumption terminal · no positions, no orders.
        </div>
        <div className="mt-2 flex items-center gap-2 rounded-md border border-border px-2 py-1.5">
          <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-accent text-[10px] font-bold text-accent-foreground">
            AD
          </span>
          <div className="min-w-0">
            <div className="truncate text-[11px] font-medium">Self-hosted</div>
            <div className="truncate text-[10px] text-muted-foreground">open source</div>
          </div>
        </div>
      </div>
    </nav>
  )
}
