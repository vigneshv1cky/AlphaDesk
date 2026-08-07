import { useEffect, useState } from "react"
import { api } from "@/lib/api"
import { Earnings } from "@/components/Earnings"

export function EarningsView() {
  const [earnings, setEarnings] = useState<{ upcoming: any[]; reported: any[] } | null>(null)

  useEffect(() => {
    const poll = () => api.earnings().then(d => setEarnings(d || { upcoming: [], reported: [] })).catch(() => {})
    poll()
    const i = setInterval(poll, 300_000) // 5 min — earnings don't change fast
    return () => clearInterval(i)
  }, [])

  return <Earnings earnings={earnings} />
}
