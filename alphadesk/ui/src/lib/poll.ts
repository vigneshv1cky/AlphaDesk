import { useEffect, useState } from "react"

/** Fetch on mount, then re-fetch on an interval. Returns the last GOOD value:
 * a failed poll leaves the previous data on screen rather than blanking a
 * widget, which on a dashboard of a dozen tiles is the difference between
 * "one source hiccuped" and "the terminal broke". */
export function usePoll<T>(fn: () => Promise<T>, ms: number): { data: T | null; error: string | null } {
  const [data, setData] = useState<T | null>(null)
  const [error, setError] = useState<string | null>(null)
  useEffect(() => {
    let alive = true
    const load = () =>
      fn()
        .then(d => { if (alive) { setData(d); setError(null) } })
        .catch(e => { if (alive) setError(String(e?.message ?? e)) })
    load()
    const t = setInterval(load, ms)
    return () => { alive = false; clearInterval(t) }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ms])
  return { data, error }
}
