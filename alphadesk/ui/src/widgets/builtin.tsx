import { Link } from "react-router-dom"
import { useEarnings, useScreener, useSystem } from "@/lib/queries"
import { Btn, Empty, Stat, TD, TH, THead, TR, Table, Widget } from "@/components/terminal"
import { registerWidget } from "@/widgets/registry"

/** The tiles AlphaDesk ships with.
 *
 * Each is an ordinary component that fetches its own data and registers
 * itself at import time. A plugin adds a tile the same way; re-registering a
 * built-in id replaces it. Ordering leaves gaps so third-party tiles can slot
 * between these without renumbering.
 */

function StatusStrip() {
  const { data: sys } = useSystem()
  const { data } = useScreener()
  const symbols = data?.symbols ?? []
  const withNews = symbols.filter(s => s.article_count > 0)
  const reporting = symbols.filter(s => s.report_date)
  return (
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
  )
}

function WindowList() {
  const screener = useScreener()
  const symbols = screener.data?.symbols ?? []
  return (
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
  )
}

function NewsTape() {
  const screener = useScreener()
  const withNews = (screener.data?.symbols ?? []).filter(s => s.article_count > 0)
  const headlines = withNews
    .flatMap(s => s.headlines.map(h => ({ ...h, symbol: s.symbol })))
    .sort((a, b) => String(b.published_at ?? "").localeCompare(String(a.published_at ?? "")))
    .slice(0, 60)
  return (
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
  )
}

function ReportingSoon() {
  const earnings = useEarnings()
  const upcoming = earnings.data?.upcoming ?? []
  return (
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
  )
}

registerWidget({ id: "status", order: 10, component: StatusStrip })
registerWidget({ id: "window", order: 20, component: WindowList })
registerWidget({ id: "news-tape", order: 30, component: NewsTape })
registerWidget({ id: "reporting-soon", order: 40, component: ReportingSoon })
