// Plain-English labels for the desk's internal jargon, so a non-trader can read the UI.

export const plainEdge = (e?: string | null): string =>
  ({
    SPILLOVER: "Ripple effect",
    MOMENTUM: "Momentum",
    THEME: "Theme",
    EARNINGS: "Earnings",
    WORLD: "World event",
  })[e ?? ""] ?? e ?? ""

// Verdict is now a CONVICTION tier — every debated name commits to a direction,
// so PASS is a thin lean the desk tracks but won't size up, not a rejection.
export const plainVerdict = (v?: string | null): string =>
  ({ STRONG: "High conviction", SOFT: "Moderate", PASS: "Thin lean" })[v ?? ""] ?? v ?? ""

// LONG = buy, expecting the price to RISE. SHORT = bet the price FALLS.
export const dirWord = (d?: string): string => (d === "LONG" ? "Buy" : "Short")
export const dirUp = (d?: string): boolean => d === "LONG"
export const dirHint = (d?: string): string =>
  d === "LONG" ? "expecting the price to rise" : "betting the price falls"
