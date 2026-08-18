import { Link } from "react-router-dom"
import { api } from "@/lib/api"
import { usePoll } from "@/lib/poll"
import { Pnl } from "@/lib/pnl"
import { Spark } from "@/components/Spark"
import { Btn, Empty, Stat, TD, TH, THead, TR, Table, Tag, Widget } from "@/components/terminal"

/** The collage — every surface of the desk visible at once.
 *
 * This is the OpenBB Workspace shape: one canvas of tiled widgets rather than
 * a nav bar where each click hides what you were just looking at. A trader
 * deciding anything needs positions, P&L, the news window and the calendar in
 * the same glance; paging between them is the thing this replaces.
 *
 * Each tile owns its own poll (see usePoll) at a cadence matched to how fast
 * that data actually moves — prices in seconds, the earnings calendar in
 * minutes — so one slow endpoint never blocks the rest of the grid. */
export default function DashboardPage() {
  const live = usePoll(() => api.live(), 15_000)
  const perf = usePoll(() => api.performance(30), 60_000)
  const screener = usePoll(() => api.screener(), 60_000)
  const system = usePoll(() => api.system(), 30_000)
  const earnings = usePoll(() => api.earnings(), 300_000)

  const rows = live.data?.live ?? []
  const p = perf.data
  const sys = system.data
  const curve = p?.curve.map(c => c.cum) ?? []
  const upcoming = earnings.data?.upcoming ?? []
  const withNews = (screener.data?.symbols ?? []).filter(s => s.article_count > 0)
  const headlines = withNews.flatMap(s => s.headlines.map(h => ({ ...h, symbol: s.symbol }))).slice(0, 40)

  return (
    <div className="collage">
      {/* ── Status strip ─────────────────────────────────────────────── */}
      <Widget span={12} bodyClassName="grid grid-cols-3 md:grid-cols-6">
        <Stat label="Market" value={sys?.market ?? "—"} />
        <Stat label="Open positions" value={sys?.open_positions ?? "—"} />
        <Stat
          label="30d return"
          value={p ? `${p.total_return >= 0 ? "+" : ""}${p.total_return.toFixed(2)}%` : "—"}
          tone={p ? (p.total_return >= 0 ? "gain" : "loss") : undefined}
          sub={p ? `${p.n} trades` : undefined}
        />
        <Stat label="Win rate" value={p?.win_rate != null ? `${p.win_rate}%` : "—"} />
        <Stat label="News today" value={sys?.news.articles_today ?? "—"} sub={sys ? `${sys.news.calls_today} AI calls` : undefined} />
        <Stat label="Uptime" value={sys ? `${Math.floor(sys.uptime_s / 3600)}h` : "—"} sub={sys?.graded != null ? `${sys.graded} graded` : undefined} />
      </Widget>

      {/* ── Live positions ───────────────────────────────────────────── */}
      <Widget
        span={7}
        title="Live positions"
        subtitle={rows.length ? `${rows.length} open` : undefined}
        actions={<Link to="/live"><Btn variant="ghost">open →</Btn></Link>}
        scroll={220}
      >
        {!live.data ? (
          <Empty>loading…</Empty>
        ) : rows.length === 0 ? (
          <Empty>no open positions</Empty>
        ) : (
          <Table>
            <THead>
              <TH>Sym</TH><TH>Dir</TH><TH align="right">Entry</TH><TH align="right">Last</TH>
              <TH align="right">P&L</TH><TH align="right">vs SPY</TH><TH>Status</TH>
            </THead>
            <tbody>
              {rows.map(r => (
                <TR key={r.id}>
                  <TD className="font-semibold">{r.symbol}</TD>
                  <TD>
                    <Tag tone={r.direction === "LONG" ? "gain" : "loss"}>{r.direction}</Tag>
                  </TD>
                  <TD align="right" mono>{r.plan_entry?.toFixed(2) ?? "—"}</TD>
                  <TD align="right" mono>{r.current?.toFixed(2) ?? "—"}</TD>
                  <TD align="right" mono><Pnl value={r.pnl_pct} /></TD>
                  <TD align="right" mono><Pnl value={r.alpha_so_far} /></TD>
                  <TD className="text-muted-foreground">{r.status}</TD>
                </TR>
              ))}
            </tbody>
          </Table>
        )}
      </Widget>

      {/* ── Equity / performance ─────────────────────────────────────── */}
      <Widget
        span={5}
        title="Equity curve"
        subtitle="30d, equal-weight"
        actions={<Link to="/performance"><Btn variant="ghost">detail →</Btn></Link>}
      >
        <div className="px-2 pt-2">
          <Spark values={curve} height={72} tone={p && p.total_return < 0 ? "loss" : "gain"} />
        </div>
        <div className="grid grid-cols-3 border-t border-grid-line">
          <Stat label="Max DD" value={p ? `${p.max_drawdown.toFixed(2)}%` : "—"} />
          <Stat label="Sharpe (trade)" value={p?.trade_sharpe ?? "—"} />
          <Stat label="Sharpe (daily)" value={p?.daily_sharpe ?? "—"} />
        </div>
        <div className="grid grid-cols-2 border-t border-grid-line">
          {["HUMAN", "MACHINE"].map(who => {
            const d = p?.by_decider?.[who]
            return (
              <Stat
                key={who}
                label={who}
                value={d?.mean_alpha != null ? `${d.mean_alpha >= 0 ? "+" : ""}${d.mean_alpha}%` : "—"}
                tone={d?.mean_alpha != null ? (d.mean_alpha >= 0 ? "gain" : "loss") : undefined}
                sub={d ? `${d.n} trades · mean α` : "no closed trades"}
              />
            )
          })}
        </div>
      </Widget>

      {/* ── Screener window ──────────────────────────────────────────── */}
      <Widget
        span={4}
        title="Window"
        subtitle={screener.data ? `${screener.data.symbols.length} symbols · ${withNews.length} with news` : undefined}
        actions={<Link to="/screener"><Btn variant="ghost">ask →</Btn></Link>}
        scroll={260}
      >
        {!screener.data ? (
          <Empty>loading…</Empty>
        ) : screener.data.symbols.length === 0 ? (
          <Empty>nothing in the window</Empty>
        ) : (
          <Table>
            <THead>
              <TH>Sym</TH><TH align="right">News</TH><TH>Reports</TH><TH></TH>
            </THead>
            <tbody>
              {screener.data.symbols.slice(0, 200).map(s => (
                <TR key={s.symbol}>
                  <TD className="font-semibold">{s.symbol}</TD>
                  <TD align="right" mono className={s.article_count ? "" : "text-muted-foreground/50"}>
                    {s.article_count || "—"}
                  </TD>
                  <TD mono className="text-muted-foreground">{s.report_date ?? "—"}</TD>
                  <TD align="right">
                    <Link to={`/trade?symbol=${encodeURIComponent(s.symbol)}`}>
                      <Btn variant="ghost">trade</Btn>
                    </Link>
                  </TD>
                </TR>
              ))}
            </tbody>
          </Table>
        )}
      </Widget>

      {/* ── News tape ────────────────────────────────────────────────── */}
      <Widget span={4} title="News tape" subtitle={`${headlines.length} headlines`} scroll={260}>
        {!screener.data ? (
          <Empty>loading…</Empty>
        ) : headlines.length === 0 ? (
          <Empty>no news in the window</Empty>
        ) : (
          <ul>
            {headlines.map((h, i) => (
              <li key={i} className="border-b border-grid-line last:border-b-0 hover:bg-muted/60">
                <a href={h.url} target="_blank" rel="noreferrer" className="block px-2 py-1">
                  <span className="num mr-1.5 text-[10px] font-semibold text-accent">{h.symbol}</span>
                  <span className="text-[11px]">{h.title}</span>
                  <span className="ml-1.5 text-[10px] text-muted-foreground">— {h.source}</span>
                </a>
              </li>
            ))}
          </ul>
        )}
      </Widget>

      {/* ── Earnings calendar ────────────────────────────────────────── */}
      <Widget
        span={4}
        title="Reporting soon"
        subtitle={upcoming.length ? `${upcoming.length} names` : undefined}
        actions={<Link to="/earnings"><Btn variant="ghost">calendar →</Btn></Link>}
        scroll={260}
      >
        {!earnings.data ? (
          <Empty>loading…</Empty>
        ) : upcoming.length === 0 ? (
          <Empty>nothing scheduled</Empty>
        ) : (
          <Table>
            <THead>
              <TH>Date</TH><TH>Sym</TH><TH>Sess</TH><TH align="right">Est EPS</TH>
            </THead>
            <tbody>
              {upcoming.slice(0, 200).map((e, i) => (
                <TR key={`${e.symbol}-${i}`}>
                  <TD mono className="text-muted-foreground">{e.report_date?.slice(5, 10)}</TD>
                  <TD className="font-semibold">{e.symbol}</TD>
                  <TD className="text-muted-foreground">{e.session ?? "—"}</TD>
                  <TD align="right" mono>{e.eps_estimate ?? "—"}</TD>
                </TR>
              ))}
            </tbody>
          </Table>
        )}
      </Widget>
    </div>
  )
}
