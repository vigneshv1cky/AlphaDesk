import { useState } from "react"

export type Theme = "light" | "dark"

function current(): Theme {
  return document.documentElement.classList.contains("dark") ? "dark" : "light"
}

// Toggle .dark on <html>, persist the choice. Initial theme is applied by the
// inline script in index.html (default dark) so there's no flash on load.
export function useTheme(): [Theme, () => void] {
  const [theme, setTheme] = useState<Theme>(current)
  const toggle = () => {
    const next: Theme = theme === "dark" ? "light" : "dark"
    document.documentElement.classList.toggle("dark", next === "dark")
    try {
      localStorage.setItem("theme", next)
    } catch {
      /* ignore */
    }
    setTheme(next)
  }
  return [theme, toggle]
}
