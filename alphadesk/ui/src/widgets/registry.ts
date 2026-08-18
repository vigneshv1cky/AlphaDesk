import type { ComponentType } from "react"

/** The dashboard widget registry.
 *
 * The collage used to be hardcoded JSX inside DashboardPage, which meant
 * adding a tile required editing the page — fine for one operator, hostile to
 * a contributor. Now a widget is a self-contained module that registers
 * itself, and the page just renders whatever is registered, in order.
 *
 * A widget owns its own data fetching (see lib/queries — shared query keys
 * mean two widgets asking for the same endpoint still make one request).
 */
export type WidgetDef = {
  /** Stable id — used for ordering and, later, for saved layouts. */
  id: string
  /** Lower sorts first. Leave gaps so a plugin can slot between built-ins. */
  order: number
  /** Grid width is NOT declared here: the component renders its own
   * `<Widget span={n}>`, so duplicating it in the registry would be a second
   * source of truth that drifts. When a layout editor exists it will override
   * the rendered span rather than the registry re-declaring it. */
  component: ComponentType
}

const registry = new Map<string, WidgetDef>()

export function registerWidget(def: WidgetDef): void {
  // Last registration wins, so a fork or plugin can replace a built-in tile
  // by re-registering its id rather than patching the page.
  registry.set(def.id, def)
}

export function widgets(): WidgetDef[] {
  return [...registry.values()].sort((a, b) => a.order - b.order || a.id.localeCompare(b.id))
}

export function unregisterWidget(id: string): boolean {
  return registry.delete(id)
}
