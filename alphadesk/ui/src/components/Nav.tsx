import { NavLink } from "react-router-dom"

const LINKS = [
  { to: "/live", label: "Live" },
  { to: "/history", label: "History" },
  { to: "/earnings", label: "Earnings" },
]

export function Nav() {
  return (
    <nav className="flex items-center gap-1">
      {LINKS.map(l => (
        <NavLink
          key={l.to}
          to={l.to}
          className={({ isActive }) =>
            `rounded-md px-3 py-1.5 text-sm font-medium transition-colors ${
              isActive
                ? "bg-indigo-600 text-white"
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
