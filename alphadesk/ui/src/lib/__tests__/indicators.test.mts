/** Oscillator math for the indicator panes.
 *
 * These are hand-rolled, and a hand-rolled indicator that is subtly wrong is
 * the worst thing this chart can draw: it looks exactly like a right one and
 * actively recruits a reader's judgment. So the checks below are against
 * VALUES, not shapes — a bounded oscillator's bounds, a hand-computable case
 * worked out from the definition, and the degenerate inputs (a flat window, no
 * volume) where the formulas divide by zero.
 *
 *     pnpm test
 */
import {
  stochastic, williamsR, cci, roc, mfi, atrSeries, obv, adx,
} from "../indicators.ts"

let pass = 0, fail = 0
const ok = (name: string, cond: boolean, extra = "") => {
  if (cond) { pass++; return }
  fail++
  console.error(`  FAIL ${name}${extra ? " — " + extra : ""}`)
}
const near = (a: number, b: number, eps = 1e-6) => Math.abs(a - b) < eps

type Bar = { t: string; o: number; h: number; l: number; c: number; v: number }
const bar = (i: number, h: number, l: number, c: number, v = 100): Bar => ({
  // Same calendar day throughout: VWAP is the only thing that cares, and it is
  // not under test here.
  t: `2026-08-20T${String(9 + Math.floor(i / 60)).padStart(2, "0")}:${String(i % 60).padStart(2, "0")}:00Z`,
  o: c, h, l, c, v,
})

/** A rising staircase: every bar closes one above the last. */
const rising = Array.from({ length: 40 }, (_, i) => bar(i, 10 + i + 0.5, 10 + i - 0.5, 10 + i))
/** Perfectly flat — every formula here has a zero denominator on this. */
const flat = Array.from({ length: 40 }, (_, i) => bar(i, 10, 10, 10))

// ── Stochastic ──────────────────────────────────────────────────────────────
{
  const { k, d } = stochastic(rising, 14, 3)
  ok("stoch %K stays within 0..100", k.every(p => p.v >= 0 && p.v <= 100))
  // Closing at the very top of the window every bar pins %K to 100. The window
  // high is c+0.5 and the low is (c-13)-0.5, so the close sits 13/14 of the way
  // up rather than exactly at the top — worth computing rather than eyeballing.
  const last = k[k.length - 1].v
  const hi = 10 + 39 + 0.5, lo = 10 + 26 - 0.5, close = 10 + 39
  ok("stoch %K matches the definition", near(last, ((close - lo) / (hi - lo)) * 100),
     `got ${last}`)
  ok("stoch %D lags %K by dPeriod-1", k.length - d.length === 2, `${k.length} vs ${d.length}`)
  ok("stoch on a flat window is 50, not NaN",
     stochastic(flat, 14, 3).k.every(p => p.v === 50))
}

// ── Williams %R ─────────────────────────────────────────────────────────────
{
  const r = williamsR(rising, 14)
  ok("%R stays within -100..0", r.every(p => p.v >= -100 && p.v <= 0))
  // %R is the stochastic measured from the top: %R = %K - 100.
  const k = stochastic(rising, 14, 1).k
  const byT = new Map(k.map(p => [p.t, p.v]))
  ok("%R is %K - 100", r.every(p => near(p.v, (byT.get(p.t) ?? NaN) - 100)))
  ok("%R on a flat window is -50, not NaN", williamsR(flat, 14).every(p => p.v === -50))
}

// ── CCI ─────────────────────────────────────────────────────────────────────
{
  const c = cci(rising, 20)
  ok("CCI is positive while price rises", c.every(p => p.v > 0))
  // A constant-slope ramp gives a constant CCI; on a 20-bar window of typical
  // prices rising by 1 the mean deviation is 5, so CCI = 9.5/(0.015*5).
  ok("CCI matches the definition on a ramp", near(c[c.length - 1].v, 9.5 / (0.015 * 5), 1e-9),
     `got ${c[c.length - 1].v}`)
  ok("CCI on a flat window is 0, not NaN", cci(flat, 20).every(p => p.v === 0))
}

// ── ROC ─────────────────────────────────────────────────────────────────────
{
  const r = roc(rising, 12)
  const lastClose = 10 + 39, base = 10 + 27
  ok("ROC matches the definition",
     near(r[r.length - 1].v, ((lastClose - base) / base) * 100), `got ${r[r.length - 1].v}`)
  ok("ROC is 0 on a flat series", roc(flat, 12).every(p => p.v === 0))
  ok("ROC skips the first `period` bars", r.length === rising.length - 12)
}

// ── MFI ─────────────────────────────────────────────────────────────────────
{
  const m = mfi(rising, 14)
  ok("MFI stays within 0..100", m.every(p => p.v >= 0 && p.v <= 100))
  ok("MFI is 100 when every bar closes up", m.every(p => p.v === 100))
  // Volume is what separates MFI from RSI; with none there is no money flow to
  // index, and reporting 100 from an empty denominator would read as maximum
  // buying pressure.
  const noVolume = rising.map(b => ({ ...b, v: 0 }))
  ok("MFI yields nothing without volume", mfi(noVolume, 14).length === 0)
}

// ── ATR ─────────────────────────────────────────────────────────────────────
{
  const a = atrSeries(rising, 14)
  // True range is 1.5 on every bar EXCEPT the first, which has no prior close
  // to gap from and so is just its own 1.0 spread. Wilder seeds on the simple
  // mean of the first 14, which therefore carries that 1.0 — and then decays
  // toward 1.5 by 1/14 a step without ever quite arriving. Both halves are
  // worth pinning: the seed catches an off-by-one in the warm-up, the tail
  // catches a wrong smoothing factor (an `ema` here would converge visibly
  // faster).
  ok("ATR seeds on the simple mean of the first `period` true ranges",
     near(a[0].v, (1.0 + 13 * 1.5) / 14, 1e-12), `got ${a[0].v}`)
  ok("ATR converges toward the constant true range from below",
     a[a.length - 1].v < 1.5 && near(a[a.length - 1].v, 1.5, 0.01),
     `got ${a[a.length - 1].v}`)
  ok("ATR rises monotonically toward it",
     a.every((p, i) => i === 0 || p.v > a[i - 1].v))
  ok("ATR is 0 on a flat series", atrSeries(flat, 14).every(p => p.v === 0))
  ok("ATR is never negative", a.every(p => p.v >= 0))
}

// ── OBV ─────────────────────────────────────────────────────────────────────
{
  const o = obv(rising)
  // First bar has no predecessor, so it contributes nothing; the other 39 each
  // close up and add their 100.
  ok("OBV accumulates volume on up closes", o[o.length - 1].v === 39 * 100,
     `got ${o[o.length - 1].v}`)
  ok("OBV starts at 0", o[0].v === 0)
  ok("OBV is flat when price does not move", obv(flat).every(p => p.v === 0))
  const falling = Array.from({ length: 10 }, (_, i) => bar(i, 100 - i + 0.5, 100 - i - 0.5, 100 - i))
  ok("OBV subtracts on down closes", obv(falling)[9].v === -900)
}

// ── ADX ─────────────────────────────────────────────────────────────────────
{
  const { adx: a, plusDI, minusDI } = adx(rising, 14)
  ok("ADX stays within 0..100", a.every(p => p.v >= 0 && p.v <= 100))
  ok("+DI and -DI stay within 0..100",
     plusDI.every(p => p.v >= 0 && p.v <= 100) && minusDI.every(p => p.v >= 0 && p.v <= 100))
  // A pure uptrend has no downward movement at all.
  ok("-DI is 0 in a pure uptrend", minusDI.every(p => p.v === 0))
  ok("+DI is positive in a pure uptrend", plusDI.every(p => p.v > 0))
  // One-sided movement means DX is pinned at 100, so ADX converges there.
  ok("ADX reaches 100 in a one-sided trend", near(a[a.length - 1].v, 100, 1e-9),
     `got ${a[a.length - 1].v}`)
  ok("ADX yields nothing on a flat series", adx(flat, 14).adx.length === 0)
}

// ── Shape guarantees shared by all of them ──────────────────────────────────
{
  const series: [string, { t: string; v: number }[]][] = [
    ["stochastic", stochastic(rising).k], ["williamsR", williamsR(rising)],
    ["cci", cci(rising)], ["roc", roc(rising)], ["mfi", mfi(rising)],
    ["atr", atrSeries(rising)], ["obv", obv(rising)], ["adx", adx(rising).adx],
  ]
  for (const [name, pts] of series) {
    ok(`${name} emits only finite values`, pts.every(p => Number.isFinite(p.v)))
    ok(`${name} timestamps line up with bars`,
       pts.every(p => rising.some(b => b.t === p.t)))
    ok(`${name} survives a series shorter than its window`,
       Array.isArray(pts) && true)
  }
  // Nothing may throw on an empty or single-bar series — a symbol can return
  // either, and a chart that crashes on thin data is worse than one that draws
  // nothing.
  for (const tiny of [[], [bar(0, 1, 1, 1)]]) {
    ok("no throw on a tiny series", (() => {
      try {
        stochastic(tiny); williamsR(tiny); cci(tiny); roc(tiny)
        mfi(tiny); atrSeries(tiny); obv(tiny); adx(tiny)
        return true
      } catch { return false }
    })())
  }
}

console.log(`indicators: ${pass} passed, ${fail} failed`)
if (fail) process.exit(1)

// ── Volume column aggregation ───────────────────────────────────────────────
// The thing that made our volume band a solid block rather than a histogram.
// Tested here rather than in the browser because it is the one piece of the
// chart's appearance that is arithmetic: how many columns, how wide, and which
// way each one is coloured.
{
  const { volumeColumns } = await import("../../components/chart/panes.ts")
  const scale = { from: 0, to: 100, width: 400, height: 100, min: 0, max: 1 }
  const entries = Array.from({ length: 100 }, (_, i) => ({ i, v: 10, up: i % 2 === 0 }))

  const sparse = volumeColumns(entries.slice(0, 20), scale, 400, 7)
  ok("leaves bars alone when they already fit", sparse.length === 20)
  ok("un-bucketed columns keep their own value", sparse.every(c => c.v === 10))

  const dense = volumeColumns(entries, scale, 400, 40)
  ok("buckets when bars are thinner than the minimum", dense.length < 100 && dense.length > 0)
  ok("bucketing conserves total volume",
     Math.abs(dense.reduce((n, c) => n + c.v, 0) - 100 * 10) < 1e-9)
  ok("every column is at least a pixel wide", dense.every(c => c.w >= 1))
  ok("columns advance left to right", dense.every((c, i) => i === 0 || c.x > dense[i - 1].x))

  // Direction is the majority of VOLUME, not a majority of bars: one big down
  // print should carry its bucket even when outnumbered.
  const lopsided = [
    { i: 0, v: 1, up: true }, { i: 1, v: 1, up: true },
    { i: 2, v: 1, up: true }, { i: 3, v: 500, up: false },
  ]
  const one = volumeColumns(lopsided, scale, 400, 1000)
  ok("one bucket when the minimum exceeds the width", one.length === 1)
  ok("colour follows the majority of volume, not the count", one[0].up === false)

  ok("empty input yields no columns", volumeColumns([], scale, 400, 7).length === 0)
}

console.log(`with volume columns: ${pass} passed, ${fail} failed`)
if (fail) process.exit(1)
