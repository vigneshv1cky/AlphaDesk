import { useEffect, useMemo, useState } from "react"

export type Theme = "light" | "dark" | "system"

function resolveSystem(): "light" | "dark" {
  if (window.matchMedia("(prefers-color-scheme: dark)").matches) return "dark"
  return "light"
}

function applied(): Theme {
  try {
    const raw = localStorage.getItem("theme")
    if (raw === "light" || raw === "dark" || raw === "system") return raw
  } catch { /* ignore */ }
  // No stored preference yet — default new visitors into the terminal's
  // native dark look rather than following the OS, which skews light.
  return "dark"
}

function apply(t: Theme) {
  const dark = t === "dark" || (t === "system" && resolveSystem() === "dark")
  document.documentElement.classList.toggle("dark", dark)
}

/** Is dark mode ACTUALLY applied right now?
 *
 * Distinct from useTheme()'s value, which can be "system" — a consumer that
 * needs a real colour (canvas, chart, anything outside CSS) has to resolve
 * that. Watches the `dark` class apply() toggles, so it stays correct when the
 * OS preference flips under "system" too. */
export function useIsDark(): boolean {
  const [dark, setDark] = useState(
    () => typeof document !== "undefined" && document.documentElement.classList.contains("dark"),
  )
  useEffect(() => {
    const root = document.documentElement
    const sync = () => setDark(root.classList.contains("dark"))
    const obs = new MutationObserver(sync)
    obs.observe(root, { attributes: true, attributeFilter: ["class"] })
    const mq = window.matchMedia("(prefers-color-scheme: dark)")
    mq.addEventListener("change", sync)
    sync()
    return () => { obs.disconnect(); mq.removeEventListener("change", sync) }
  }, [])
  return dark
}

export function useTheme(): [Theme, () => void] {
  const [theme, setTheme] = useState<Theme>(applied)
  useEffect(() => { apply(theme) }, [theme])
  const cycle = () => {
    const order: Theme[] = ["light", "dark", "system"]
    const next = order[(order.indexOf(theme) + 1) % order.length]
    apply(next)
    try { localStorage.setItem("theme", next) } catch { /* ignore */ }
    setTheme(next)
  }
  return [theme, cycle]
}


/** The colours the chart renderer paints with, resolved from the design
 * tokens and re-read whenever the theme flips.
 *
 * Resolved rather than passed as `var(--x)` because SVG attributes accept a
 * var() but the renderer also needs these values for measurement and for the
 * axis tag fills, and half-resolved colours are the kind of thing that works
 * in one theme and not the other.
 */
export function useChartTheme() {
  const dark = useIsDark()
  return useMemo(() => {
    const read = (name: string, fallback: string) => {
      if (typeof document === "undefined") return fallback
      const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim()
      return v || fallback
    }
    return {
      gain: read("--gain", "#34d98c"),
      loss: read("--loss", "#fe6864"),
      accent: read("--accent", "#3c83f5"),
      grid: read("--chart-grid", "rgba(255,255,255,0.06)"),
      text: read("--muted-foreground", "#969a9e"),
      // The crosshair chips. They used the muted TEXT colour as a fill with
      // black lettering, which is a dark slab in the light theme and unreadable
      // in it — black on #5b636a. A muted SURFACE with the normal foreground
      // reads the same way in both themes and sits back where a readout of the
      // cursor's position belongs, behind the live price tag rather than
      // shouting over it.
      tagBg: read("--muted", "#26262a"),
      tagFg: read("--foreground", "#e0e5ea"),
      tagBorder: read("--border", "#26262a"),
    }
    // dark is the dependency: the tokens change with it.
  }, [dark])
}
