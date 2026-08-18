import { Link } from "react-router-dom"
import { useEarnings, useScreener, useSystem } from "@/lib/queries"
import { Btn, Empty, Stat, TD, TH, THead, TR, Table, Widget } from "@/components/terminal"

/** The collage — everything the desk is watching, on one canvas.
 *
 * This is the OpenBB Workspace shape: tiled widgets rather than a nav bar
 * where each click hides what you were just looking at.
 *
 * It used to lead with live positions and an equity curve. Those were the
 * measurement layer and went with it — AlphaDesk consumes market information,
 * it does not hold positions or score them. What's left is the stuff you
 * actually read: what's in the window, what's being said, and who reports
 * next.
 *
 * Each tile owns its own poll at a cadence matched to how fast that data
 * really moves, so one slow endpoint never blocks the grid. */
export default function DashboardPage() {
  const screener = useScreener()
  const system = useSystem()
  const earnings = useEarnings()

  const sys = system.data
  const symbols = screener.data?.symbols ?? []
  const withNews = symbols.filter(s => s.article_count > 0)
  const reporting = symbols.filter(s => s.report_date)
  const upcoming = earnings.data?.upcoming ?? []
  const headlines = withNews
    .flatMap(s => s.headlines.map(h => ({ ...h, symbol: s.symbol })))
    .sort((a, b) => String(b.published_at ?? "").localeCompare(String(a.published_at ?? "")))
    .slice(0, 60)

  return (
    <div className="collage">
      <Widget span={12} bodyClassName="grid grid-cols-3 md:grid-cols-6">
        <Stat label="Market" value={sys?.market ?? "—"} />
        <Stat label="In window" value={symbols.length || "—"} sub="symbols" />
        <Stat label="With news" value={withNews.length || "—"} sub="last 36h" />
        <Stat label="Reporting" value={reporting.length || "—"} sub="inside the horizon" />
        <Stat
          label="News today"
          value={sys?.news.articles_today ?? "—"}
          sub={sys ? `${sys.news.calls_today} AI calls` : undefined}
        />
        <Stat label="Uptime" value={sys ? `${Math.floor(sys.uptime_s / 3600)}h` : "—"} />
      </Widget>

      <Widget
        span={5}
        title="Window"
        subtitle={screener.data ? `${symbols.length} symbols · alphabetical, nothing ranked` : "loading…"}
        actions={<Link to="/screener"><Btn variant="ghost">filter →</Btn></Link>}
        scroll={340}
      >
        {!screener.data ? (
          <Empty>loading…</Empty>
        ) : symbols.length === 0 ? (
          <Empty>nothing in the window</Empty>
        ) : (
          <Table>
            <THead>
              <TH>Sym</TH><TH align="right">News</TH><TH>Reports</TH><TH></TH>
            </THead>
            <tbody>
              {symbols.slice(0, 250).map(s => (
                <TR key={s.symbol}>
                  <TD className="font-semibold">{s.symbol}</TD>
                  <TD align="right" mono className={s.article_count ? "" : "text-muted-foreground/50"}>
                    {s.article_count || "—"}
                  </TD>
                  <TD mono className="text-muted-foreground">{s.report_date ?? "—"}</TD>
                  <TD align="right">
                    <Link to={`/chart?symbol=${encodeURIComponent(s.symbol)}`}>
                      <Btn variant="ghost">chart</Btn>
                    </Link>
                  </TD>
                </TR>
              ))}
            </tbody>
          </Table>
        )}
      </Widget>

      <Widget span={7} title="News tape" subtitle={`${headlines.length} headlines, newest first`} scroll={340}>
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

      <Widget
        span={12}
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
              <TH>Date</TH><TH>Sym</TH><TH>Sess</TH><TH align="right">Est EPS</TH><TH></TH>
            </THead>
            <tbody>
              {upcoming.slice(0, 250).map((e, i) => (
                <TR key={`${e.symbol}-${i}`}>
                  <TD mono className="text-muted-foreground">{e.report_date?.slice(5, 10)}</TD>
                  <TD className="font-semibold">{e.symbol}</TD>
                  <TD className="text-muted-foreground">{e.session ?? "—"}</TD>
                  <TD align="right" mono>{e.eps_estimate ?? "—"}</TD>
                  <TD align="right">
                    <Link to={`/chart?symbol=${encodeURIComponent(e.symbol)}`}>
                      <Btn variant="ghost">chart</Btn>
                    </Link>
                  </TD>
                </TR>
              ))}
            </tbody>
          </Table>
        )}
      </Widget>
    </div>
  )
}
