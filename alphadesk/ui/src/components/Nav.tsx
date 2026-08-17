import { NavLink } from "react-router-dom"

// The actual product loop: look → decide → get graded. Visually foregrounded
// — everything else is back-office (positions ledger, calendar, health).
const PRIMARY = [
  { to: "/screener", label: "Screener" },
  { to: "/trade", label: "Trade" },
  { to: "/performance", label: "Performance" },
]

const SECONDARY = [
  { to: "/live", label: "Live" },
  { to: "/history", label: "History" },
  { to: "/earnings", label: "Earnings" },
  { to: "/system", label: "Health" },
]

function NavGroup({ links, primary }: { links: typeof PRIMARY; primary: boolean }) {
  return (
    <nav className="flex items-center gap-0.5">
      {links.map(l => (
        <NavLink
          key={l.to}
          to={l.to}
          className={({ isActive }) =>
            `rounded-md px-2.5 py-1.5 font-medium transition-colors ${
              primary ? "text-[13px]" : "text-[12px]"
            } ${
              isActive
                ? "bg-indigo-600 text-white"
                : primary
                  ? "text-foreground/80 hover:bg-muted hover:text-foreground"
                  : "text-muted-foreground hover:bg-muted hover:text-foreground"
            }`
          }
        >
          {l.label}
        </NavLink>
      ))}
    </nav>
  )
}

export function Nav() {
  return (
    <div className="flex items-center gap-2">
      <NavGroup links={PRIMARY} primary />
      <span className="h-4 w-px bg-border" />
      <NavGroup links={SECONDARY} primary={false} />
    </div>
  )
}
