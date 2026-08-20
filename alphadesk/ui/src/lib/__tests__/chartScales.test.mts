/** Scale math for the chart renderer.
 *
 * The renderer draws nothing that does not pass through these functions, so a
 * bug here is a chart that is subtly, confidently wrong — an axis off by a
 * pixel is invisible; a log scale that is not really logarithmic is not.
 *
 * Plain assertions over a runner because the frontend has no test harness and
 * one dependency for one file is a poor trade:
 *
 *     npx tsx src/lib/__tests__/chartScales.test.mts
 */
import { paneExtent } from "../../components/chart/panes"
import {
  indexToX, xToIndex, priceToY, yToPrice, niceStep, priceTicks,
  padRange, zoomAt, visibleExtent,
} from "../chartScales"

let pass = 0, fail = 0
const ok = (name, cond, extra = "") => {
  if (cond) { pass++; console.log(`  ✓ ${name}`) }
  else { fail++; console.log(`  ✗ ${name} ${extra}`) }
}
const near = (a, b, eps = 1e-6) => Math.abs(a - b) < eps

const s = { from: 0, to: 100, width: 1000, height: 400, min: 100, max: 200 }

console.log("index <-> x")
ok("left edge maps to 0", near(indexToX(s, 0), 0))
ok("right edge maps to width", near(indexToX(s, 100), 1000))
ok("round trips", near(xToIndex(s, indexToX(s, 37.5)), 37.5))

console.log("price <-> y (inverted)")
ok("max sits at the top", near(priceToY(s, 200), 0))
ok("min sits at the bottom", near(priceToY(s, 100), 400))
ok("midpoint centres", near(priceToY(s, 150), 200))
ok("round trips", near(yToPrice(s, priceToY(s, 172.5)), 172.5))
ok("a flat series centres rather than pinning to an edge",
   near(priceToY({ ...s, min: 5, max: 5 }, 5), 200))

console.log("log scale")
const ls = { ...s, min: 10, max: 1000 }
ok("equal ratios take equal space",
   near(priceToY(ls, 100, true), 200, 1e-6),
   `got ${priceToY(ls, 100, true)}`)
ok("log round trips", near(yToPrice(ls, priceToY(ls, 250, true), true), 250, 1e-6))

console.log("ticks")
ok("nice steps snap to 1/2/2.5/5", niceStep(0.3) === 0.5 && niceStep(3) === 5 && niceStep(12) === 20,
   `${niceStep(0.3)} ${niceStep(3)} ${niceStep(12)}`)
const t = priceTicks(100, 200)
ok("ticks land inside the range", t.every(v => v >= 100 && v <= 200))
ok("ticks are evenly spaced", new Set(t.slice(1).map((v, i) => (v - t[i]).toFixed(6))).size === 1)
ok("an inverted range yields none", priceTicks(200, 100).length === 0)
ok("a degenerate range does not hang", priceTicks(5, 5).length === 0)

console.log("padding")
ok("pads both sides", (() => { const p = padRange(100, 200); return p.min < 100 && p.max > 200 })())
ok("a flat range still gets width", (() => { const p = padRange(50, 50); return p.max > p.min })())

console.log("zoom")
const z = zoomAt(s, 50, 0.5, 100)
ok("zooming in narrows the span", (z.to - z.from) < 100)
ok("the anchored bar stays put",
   near(indexToX({ ...s, ...z }, 50), indexToX(s, 50), 0.5),
   `${indexToX({ ...s, ...z }, 50)} vs ${indexToX(s, 50)}`)
ok("cannot zoom past a floor", (() => {
  let v = { from: 0, to: 100 }
  for (let i = 0; i < 40; i++) v = zoomAt({ ...s, ...v }, 50, 0.5, 100)
  return (v.to - v.from) >= 5
})())

console.log("visible extent")
const bars = Array.from({ length: 50 }, (_, i) => ({ h: 100 + i, l: 90 + i }))
const e = visibleExtent(bars, 10, 20)
ok("tracks only what is on screen", e.min === 100 && e.max === 120, JSON.stringify(e))
ok("an empty series does not explode", (() => { const x = visibleExtent([], 0, 10); return Number.isFinite(x.min) })())

console.log("pane extents")
const hist = (vals) => ({ id: "x", height: 60, series: [{ kind: "histogram", color: "#000",
  points: vals.map((v, i) => ({ t: String(i), v })) }] })
const lineP = (vals) => ({ id: "x", height: 60, series: [{ kind: "line", color: "#000",
  points: vals.map((v, i) => ({ t: String(i), v })) }] })
const a = paneExtent(hist([100, 500, 900]))
ok("a positive histogram floors at exactly zero", a.min === 0, JSON.stringify(a))
ok("and still pads its top", a.max > 900)
const b = paneExtent(hist([-50, 20, 80]))
ok("a signed histogram (MACD) pads both ways", b.min < -50 && b.max > 80)
ok("a line pane pads both sides", (() => { const c = paneExtent(lineP([10, 20])); return c.min < 10 && c.max > 20 })())
ok("a fixed range (RSI 0-100) is untouched",
   (() => { const d = paneExtent({ id: "rsi", height: 60, range: { min: 0, max: 100 }, series: [] })
            return d.min === 0 && d.max === 100 })())

console.log(`\n${pass} passed, ${fail} failed`)
process.exit(fail ? 1 : 0)
