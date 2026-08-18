/** Inline sparkline. Deliberately hand-drawn SVG rather than a chart library:
 * at 40px tall there are no axes, ticks, or tooltips to justify the weight,
 * and lightweight-charts (already a dependency for the real price chart) is
 * far too heavy for a dashboard tile. */
export function Spark({
  values, height = 40, tone,
}: {
  values: number[]
  height?: number
  tone?: "gain" | "loss"
}) {
  if (values.length < 2) {
    return <div style={{ height }} className="flex items-center justify-center text-[10px] text-muted-foreground">no series</div>
  }
  const min = Math.min(...values)
  const max = Math.max(...values)
  const span = max - min || 1
  const W = 100
  const pts = values.map((v, i) => {
    const x = (i / (values.length - 1)) * W
    const y = height - ((v - min) / span) * (height - 2) - 1
    return `${x.toFixed(2)},${y.toFixed(2)}`
  })
  const color = tone === "loss" ? "var(--loss)" : tone === "gain" ? "var(--gain)" : "var(--accent)"
  // Zero line only when the series actually crosses zero — otherwise it would
  // sit on an edge and read as a chart border.
  const zeroY = min < 0 && max > 0 ? height - ((0 - min) / span) * (height - 2) - 1 : null
  return (
    <svg viewBox={`0 0 ${W} ${height}`} preserveAspectRatio="none" style={{ height }} className="w-full">
      {zeroY !== null && (
        <line x1="0" y1={zeroY} x2={W} y2={zeroY} stroke="var(--grid-line)" strokeWidth="1" vectorEffect="non-scaling-stroke" />
      )}
      <polyline
        points={pts.join(" ")}
        fill="none"
        stroke={color}
        strokeWidth="1.25"
        vectorEffect="non-scaling-stroke"
      />
    </svg>
  )
}
