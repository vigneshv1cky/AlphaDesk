import type { EarningsRow, Stats, TokenRow } from "@/lib/api"
import { Ledger } from "@/components/Ledger"
import { Earnings } from "@/components/Earnings"
import { Activity } from "@/components/Activity"
import { LiveTracker } from "@/components/LiveTracker"
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
  // Base UI Tabs: keyboard arrow-nav, roving focus, and ARIA roles for free;
  // inactive panels unmount, so each tab's data loads only when it's the view.
  return (
    <Tabs defaultValue="live" className="gap-4">
      <TabsList className="h-9 bg-card p-1">
        <TabsTrigger value="live" className="px-3 text-sm">
          Live
        </TabsTrigger>
        <TabsTrigger value="record" className="px-3 text-sm">
          Track record
        </TabsTrigger>
        <TabsTrigger value="calendar" className="px-3 text-sm">
          Calendar
        </TabsTrigger>
        <TabsTrigger value="usage" className="px-3 text-sm">
          Usage
        </TabsTrigger>
      </TabsList>
      <TabsContent value="live">
        <LiveTracker />
      </TabsContent>
      <TabsContent value="record">
        <Ledger stats={stats} onSelect={onSelect} />
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
