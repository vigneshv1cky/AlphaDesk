import { NavLink } from "react-router-dom"

/** Tab-strip nav, not pill buttons. On a terminal the nav is chrome: it should
 * read as a row of tabs sharing the header's baseline, with the active one
 * marked by an accent underline rather than a filled rounded chip. */
const LINKS = [
  { to: "/dashboard", label: "Dashboard" },
  { to: "/screener", label: "Screener" },
  { to: "/filings", label: "Filings" },
  { to: "/research", label: "Research" },
  { to: "/trade", label: "Trade" },
  { to: "/performance", label: "Performance" },
  { to: "/live", label: "Live" },
  { to: "/history", label: "History" },
  { to: "/earnings", label: "Earnings" },
  { to: "/system", label: "Health" },
]

export function Nav() {
  return (
    <nav className="flex min-w-0 items-stretch overflow-x-auto">
      {LINKS.map(l => (
        <NavLink
          key={l.to}
          to={l.to}
          className={({ isActive }) =>
            `flex shrink-0 items-center border-b-2 px-2.5 text-[11px] font-medium uppercase tracking-[0.04em] transition-colors ${
              isActive
                ? "border-accent text-foreground"
                : "border-transparent text-muted-foreground hover:bg-muted hover:text-foreground"
            }`
          }
        >
          {l.label}
        </NavLink>
      ))}
    </nav>
  )
}
