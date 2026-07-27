import type { EarningsRow, Stats, TokenRow } from "@/lib/api"
import { Ledger } from "@/components/Ledger"
import { Earnings } from "@/components/Earnings"
import { Activity } from "@/components/Activity"
import { LiveTracker } from "@/components/LiveTracker"
import { Sessions } from "@/components/Sessions"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"

export function RightRail({
  stats,
  tokens,
  earnings,
  onSelect,
}: {
  stats: Stats | null
  tokens: TokenRow[]
  earnings?: { upcoming: EarningsRow[]; reported: EarningsRow[] }
  onSelect: (id: number) => void
}) {
  const trigger =
    "px-3 text-sm data-active:bg-indigo-600 data-active:text-white dark:data-active:border-transparent dark:data-active:bg-indigo-600 dark:data-active:text-white"
  return (
    <Tabs defaultValue="live" className="gap-4">
      <TabsList className="h-9 bg-card p-1">
        <TabsTrigger value="live" className={trigger}>
          Live
        </TabsTrigger>
        <TabsTrigger value="record" className={trigger}>
          Track record
        </TabsTrigger>
        <TabsTrigger value="sessions" className={trigger}>
          Sessions
        </TabsTrigger>
        <TabsTrigger value="calendar" className={trigger}>
          Calendar
        </TabsTrigger>
        <TabsTrigger value="usage" className={trigger}>
          Usage
        </TabsTrigger>
      </TabsList>
      <TabsContent value="live">
        <LiveTracker />
      </TabsContent>
      <TabsContent value="record">
        <Ledger stats={stats} onSelect={onSelect} />
      </TabsContent>
      <TabsContent value="sessions">
        <Sessions onSelect={onSelect} />
      </TabsContent>
      <TabsContent value="calendar">
        <Earnings earnings={earnings} />
      </TabsContent>
      <TabsContent value="usage">
        <Activity tokens={tokens} />
      </TabsContent>
    </Tabs>
  )
}
