import { NavLink } from "react-router-dom"

const LINKS = [
  { to: "/live", label: "Live" },
  { to: "/history", label: "History" },
  { to: "/pre", label: "Pre-Market" },
  { to: "/open", label: "Open" },
  { to: "/after", label: "After" },
  { to: "/earnings", label: "Earnings" },
  { to: "/system", label: "Health" },
]

export function Nav() {
  return (
    <nav className="flex items-center gap-0.5">
      {LINKS.map(l => (
        <NavLink
          key={l.to}
          to={l.to}
          className={({ isActive }) =>
            `rounded-md px-2.5 py-1.5 text-[13px] font-medium transition-colors ${
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
