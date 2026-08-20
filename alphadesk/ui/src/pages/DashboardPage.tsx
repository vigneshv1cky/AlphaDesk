import "@/widgets/builtin"          // registers the shipped tiles
import "@/widgets/chart"            // the price tile
import "@/widgets/market"           // quote + movers
import { widgets } from "@/widgets/registry"
import { ViewHeader } from "@/components/ViewHeader"

/** The collage — every tile the deployment has, on one canvas.
 *
 * This page deliberately knows nothing about what it renders. Tiles come from
 * the widget registry, so adding one means writing a module, not editing this
 * file — which is the difference between a dashboard one person maintains and
 * one other people can extend.
 *
 * Each tile owns its own polling; shared query keys mean two tiles reading the
 * same endpoint still produce one request.
 */
export default function DashboardPage() {
  return (
    <>
      <ViewHeader title="Markets" />
      <div className="collage">
      {widgets().map(w => {
        const W = w.component
        return <W key={w.id} />
      })}
      </div>
    </>
  )
}
