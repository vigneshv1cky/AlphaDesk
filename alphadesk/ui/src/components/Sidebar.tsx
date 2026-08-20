import { NavLink } from "react-router-dom"
import {
  Activity, CalendarDays, LineChart, Newspaper, Star, Wrench,
} from "lucide-react"

/** Left rail — sectioned, icon-led, with an identity card pinned to the
 * bottom. Mirrors AlphaSpace's shape: a VIEWS group of surfaces, a THEMES
 * group of saved symbol sets, and account chrome underneath.
 *
 * Vertical rather than top tabs because a terminal's horizontal band is spoken
 * for by the ticker strip, and a list grows without wrapping.
 */
const VIEWS = [
  { to: "/markets", label: "Markets", Icon: Activity },
  // Analysis is the one-company surface — chart AND filings, sharing a single
  // symbol. They used to be separate routes with separate inputs, which let
  // the terminal show one company's chart beside another's 10-K.
  { to: "/analysis", label: "Analysis", Icon: LineChart },
  { to: "/news", label: "News", Icon: Newspaper },
  { to: "/portfolio", label: "My Portfolio", Icon: Star },
  { to: "/earnings", label: "Earnings Hub", Icon: CalendarDays },
]

/** Themes are pre-set symbol groups. They deep-link into the news window's
 * filter rather than being a separate surface — the same list, narrowed. */
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
            to={`/news?q=${encodeURIComponent(q)}`}
            className="mx-2 flex items-center gap-2.5 rounded-md px-2 py-[7px] text-[12px] text-muted-foreground transition-colors hover:bg-muted/60 hover:text-foreground"
          >
            <Icon className="h-[15px] w-[15px] shrink-0" />
            {label}
          </NavLink>
        ))}
      </Section>

      <div className="flex-1" />

      <div className="border-t border-border p-3">
        <NavLink
          to="/system"
          className={({ isActive }) =>
            `mb-2 flex items-center gap-2 text-[11px] transition-colors ${
              isActive ? "text-foreground" : "text-muted-foreground hover:text-foreground"
            }`
          }
        >
          <Wrench className="h-[13px] w-[13px] shrink-0" />
          Health
        </NavLink>
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
