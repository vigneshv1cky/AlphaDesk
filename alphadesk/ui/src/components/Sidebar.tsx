import { NavLink } from "react-router-dom"

/** Left rail navigation — views, then the AI surfaces, then health.
 *
 * A sidebar rather than top tabs because a terminal's horizontal space belongs
 * to data: tabs across the top compete with the ticker strip for the same
 * band, and a vertical list scales to more views without wrapping.
 */
const VIEWS = [
  { to: "/dashboard", label: "Markets" },
  { to: "/screener", label: "Screener" },
  { to: "/chart", label: "Charts" },
  { to: "/earnings", label: "Earnings" },
  { to: "/filings", label: "Filings" },
]

const SYSTEM = [{ to: "/system", label: "Health" }]

function Group({ title, links }: { title: string; links: typeof VIEWS }) {
  return (
    <div className="py-1">
      <div className="px-2 py-1 text-[9px] font-semibold uppercase tracking-[0.1em] text-muted-foreground/60">
        {title}
      </div>
      {links.map(l => (
        <NavLink
          key={l.to}
          to={l.to}
          className={({ isActive }) =>
            `block border-l-2 px-2 py-[5px] text-[11px] transition-colors ${
              isActive
                ? "border-accent bg-accent/10 font-medium text-foreground"
                : "border-transparent text-muted-foreground hover:bg-muted hover:text-foreground"
            }`
          }
        >
          {l.label}
        </NavLink>
      ))}
    </div>
  )
}

export function Sidebar() {
  return (
    <nav className="flex w-[150px] shrink-0 flex-col border-r border-border bg-panel">
      <Group title="Views" links={VIEWS} />
      <div className="mx-2 h-px bg-border" />
      <Group title="System" links={SYSTEM} />
      <div className="flex-1" />
      <div className="border-t border-border px-2 py-1.5 text-[9px] leading-relaxed text-muted-foreground/60">
        Consumption terminal.
        <br />
        No positions, no orders.
      </div>
    </nav>
  )
}
