"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { Bar, BarChart, CartesianGrid, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

type Metric = {
  period_end: string; start_date?: string; fiscal_period?: string; filed?: string;
  value: number; yoy_pct: number | null; qoq_pct?: number | null;
  ttm_value?: number | null; ttm_yoy_pct?: number | null; ttm_qoq_pct?: number | null;
  source?: string; concept?: string;
};
type DashboardData = {
  company: { ticker: string; cik: number; name: string; exchange: string };
  market: {
    market_price: number | null; forward_pe: number | null;
    forward_pe_status: "available" | "not_meaningful" | "unavailable";
    forward_eps: number | null; forward_period_end: string | null;
    forward_pe_analysts: number | null; market_source: string | null;
    fetched_at: string | null;
  };
  revenue_concept: string; income_concept: string;
  eps_concept?: string; revenue: Metric[]; net_income: Metric[]; eps?: Metric[];
  cache: Record<string, string>;
};

function money(value: number | null | undefined) {
  if (value == null || Number.isNaN(value)) return "—";
  const sign = value < 0 ? "-" : "";
  const x = Math.abs(value);
  if (x >= 1e12) return `${sign}$${(x / 1e12).toFixed(2)}T`;
  if (x >= 1e9) return `${sign}$${(x / 1e9).toFixed(2)}B`;
  if (x >= 1e6) return `${sign}$${(x / 1e6).toFixed(2)}M`;
  return `${sign}$${x.toLocaleString(undefined, { maximumFractionDigits: 0 })}`;
}
function dollarsPerShare(value: number | null | undefined) {
  return value == null || Number.isNaN(value) ? "—" : `$${value.toFixed(2)}`;
}
function pct(value: number | null | undefined) { return value == null ? "—" : `${value.toFixed(1)}%`; }
function quarterLabel(date: string) { const d = new Date(`${date}T00:00:00Z`); return `${d.getUTCFullYear()} Q${Math.floor(d.getUTCMonth()/3)+1}`; }
function shortPeriodLabel(row: Metric) {
  const fiscal = row.fiscal_period?.match(/^(\d{4})\s+(Q\d)$/);
  if (fiscal) return `${fiscal[2]} ’${fiscal[1].slice(-2)}`;
  const calendar = quarterLabel(row.period_end).match(/^(\d{4})\s+(Q\d)$/);
  return calendar ? `${calendar[2]} ’${calendar[1].slice(-2)}` : row.period_end;
}

function MetricTrendPanel({ title, rows, color, valueFormatter = money, note }: {
  title: string; rows: Metric[]; color: string;
  valueFormatter?: (value: number | null | undefined) => string;
  note?: string;
}) {
  const [amountMode, setAmountMode] = useState<"quarterly" | "ttm">("quarterly");
  const [growthMode, setGrowthMode] = useState<"yoy" | "qoq">("yoy");
  const chartData = useMemo(() => rows.map(row => ({
    label: shortPeriodLabel(row),
    amount: amountMode === "quarterly" ? row.value : row.ttm_value ?? null,
    growth: amountMode === "quarterly"
      ? (growthMode === "yoy" ? row.yoy_pct : row.qoq_pct ?? null)
      : (growthMode === "yoy" ? row.ttm_yoy_pct : row.ttm_qoq_pct),
  })), [rows, amountMode, growthMode]);
  const amountLabel = amountMode === "quarterly" ? `Quarterly ${title}` : `${title} TTM`;
  const growthLabel = `${amountMode === "quarterly" ? `Quarterly ${title}` : `${title} TTM`} ${growthMode.toUpperCase()} change`;

  const amountToggle = <div className="segmented-group" role="group" aria-label={`${title} amount period`}>
    <span className="control-label">Amount</span>
    <div className="segmented-control">
      <button type="button" aria-pressed={amountMode === "quarterly"} onClick={() => setAmountMode("quarterly")}>Quarterly</button>
      <button type="button" aria-pressed={amountMode === "ttm"} onClick={() => setAmountMode("ttm")}>TTM</button>
    </div>
  </div>;
  const growthToggle = <div className="segmented-group" role="group" aria-label={`${title} growth comparison`}>
    <span className="control-label">Growth</span>
    <div className="segmented-control">
      <button type="button" aria-pressed={growthMode === "yoy"} onClick={() => setGrowthMode("yoy")}>YoY</button>
      <button type="button" aria-pressed={growthMode === "qoq"} onClick={() => setGrowthMode("qoq")}>QoQ</button>
    </div>
  </div>;

  return <section className="section trend-panel">
    <header className="trend-panel-header"><div><h2 className="trend-panel-title">{title} trends</h2>{note && <p className="trend-panel-note">{note}</p>}</div>{amountToggle}</header>
    <div className="trend-chart-stack">
      <div className="card trend-chart-card">
        <header className="trend-chart-header"><h3 className="section-title">{amountLabel}</h3></header>
        <div className="trend-chart-wrap"><ResponsiveContainer width="100%" height="100%"><BarChart data={chartData} margin={{ top: 4, right: 8, left: 4, bottom: 8 }}>
          <CartesianGrid stroke="#27272a" strokeDasharray="3 3" />
          <XAxis dataKey="label" tick={{ fill: "#a1a1aa", fontSize: 11 }} interval="preserveStartEnd" />
          <YAxis tick={{ fill: "#a1a1aa", fontSize: 11 }} tickFormatter={v => valueFormatter(Number(v))} width={86} />
          <Tooltip contentStyle={{ background: "#18181b", border: "1px solid #3f3f46", borderRadius: 8 }} formatter={v => [v == null ? "—" : valueFormatter(Number(v)), amountLabel]} />
          <ReferenceLine y={0} stroke="#52525b" />
          <Bar dataKey="amount" fill={color} radius={[4, 4, 0, 0]} maxBarSize={52} />
        </BarChart></ResponsiveContainer></div>
      </div>
      <div className="card trend-chart-card">
        <header className="trend-chart-header"><h3 className="section-title">{growthLabel}</h3>{growthToggle}</header>
        <div className="trend-chart-wrap"><ResponsiveContainer width="100%" height="100%"><BarChart data={chartData} margin={{ top: 4, right: 8, left: 2, bottom: 8 }}>
          <CartesianGrid stroke="#27272a" strokeDasharray="3 3" />
          <XAxis dataKey="label" tick={{ fill: "#a1a1aa", fontSize: 11 }} interval="preserveStartEnd" />
          <YAxis tick={{ fill: "#a1a1aa", fontSize: 11 }} tickFormatter={v => `${v}%`} width={54} />
          <Tooltip contentStyle={{ background: "#18181b", border: "1px solid #3f3f46", borderRadius: 8 }} formatter={v => [v == null ? "—" : `${Number(v).toFixed(1)}%`, growthMode.toUpperCase()]} />
          <ReferenceLine y={0} stroke="#71717a" />
          <Bar dataKey="growth" fill={color} radius={[4, 4, 0, 0]} maxBarSize={52} />
        </BarChart></ResponsiveContainer></div>
      </div>
    </div>
  </section>;
}

function forwardPeNote(market: DashboardData["market"]) {
  const source = market.market_source ?? "Estimate source unavailable";
  if (market.forward_pe_status === "not_meaningful") {
    const eps = market.forward_eps == null ? "is non-positive" : `is ${market.forward_eps.toFixed(2)}`;
    const period = market.forward_period_end ? ` through ${market.forward_period_end}` : "";
    return `Forecast EPS ${eps} · ${source}${period}`;
  }
  const horizon = market.forward_period_end ? ` · next 4 quarters through ${market.forward_period_end}` : "";
  const analysts = market.forward_pe_analysts ? ` · ${market.forward_pe_analysts} analysts` : "";
  return `${source}${horizon}${analysts}`;
}

export default function Dashboard() {
  const [ticker, setTicker] = useState("AAPL");
  const [years, setYears] = useState("3");
  const [data, setData] = useState<DashboardData | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const requestId = useRef(0);

  async function load(symbol = ticker, history = years) {
    const currentRequest = ++requestId.current;
    setLoading(true); setError("");
    try {
      const response = await fetch(`/api/dashboard?ticker=${encodeURIComponent(symbol)}&years=${history}`, { cache: "no-store" });
      const body = await response.json();
      if (!response.ok) throw new Error(body.detail || "Request failed");
      if (currentRequest === requestId.current) setData(body);
    } catch (e) {
      if (currentRequest === requestId.current) setError(e instanceof Error ? e.message : "Could not load dashboard");
    } finally {
      if (currentRequest === requestId.current) setLoading(false);
    }
  }
  useEffect(() => { void load(); }, []);

  const table = useMemo(() => {
    const map = new Map<string, { end:string; fiscal?:string; revenue?:Metric; income?:Metric }>();
    for (const row of data?.revenue ?? []) map.set(row.period_end, { ...(map.get(row.period_end) ?? { end: row.period_end }), revenue: row, fiscal: row.fiscal_period });
    for (const row of data?.net_income ?? []) map.set(row.period_end, { ...(map.get(row.period_end) ?? { end: row.period_end }), income: row, fiscal: map.get(row.period_end)?.fiscal ?? row.fiscal_period });
    return [...map.values()].sort((a,b) => b.end.localeCompare(a.end));
  }, [data]);
  const latest = data?.revenue.at(-1);
  const epsRows = data?.eps ?? [];

  return <main className="container">
    <header className="header">
      <div><div className="eyebrow">Local • SEC EDGAR • SQLite</div><h1>SEC Financial Dashboard</h1><div className="subtitle">Quarterly fundamentals with a local cache. No cloud database required.</div></div>
      <div className="controls">
        <div className="field"><label>Ticker</label><input className="input" value={ticker} onChange={e => setTicker(e.target.value.toUpperCase())} onKeyDown={e => e.key === "Enter" && void load()} /></div>
        <div className="field"><label>History</label><select className="select" value={years} onChange={e => { const nextYears = e.target.value; setYears(nextYears); void load(ticker, nextYears); }}><option value="2">2 years</option><option value="3">3 years</option><option value="4">4 years</option><option value="5">5 years</option></select></div>
        <button className="button" disabled={loading} onClick={() => void load()}>{loading ? "Loading…" : "Refresh"}</button>
      </div>
    </header>

    {error && <div className="error">{error}</div>}
    {data && <>
      <div className="card" style={{ marginBottom: 14 }}><strong>{data.company.name}</strong> <span className="badge">{data.company.ticker}</span><span className="badge" style={{ marginLeft: 6 }}>{data.company.exchange || "SEC"}</span></div>
      <section className="grid">
        <div className="card"><div className="metric-label">Current stock price</div><div className="metric-value">{data.market.market_price == null ? "—" : `$${data.market.market_price.toFixed(2)}`}</div><div className="metric-note">Market snapshot cached 15 min</div></div>
        <div className="card"><div className="metric-label">Latest quarterly revenue</div><div className="metric-value">{money(latest?.value)}</div><div className="metric-note">{latest?.period_end ?? "—"}</div></div>
        <div className="card"><div className="metric-label">Latest revenue YoY</div><div className="metric-value">{pct(latest?.yoy_pct)}</div><div className="metric-note">Compared with 4 quarters prior</div></div>
        <div className="card"><div className="metric-label">Forward P/E</div><div className="metric-value">{data.market.forward_pe_status === "not_meaningful" ? "N/M" : data.market.forward_pe == null ? "—" : data.market.forward_pe.toFixed(2)}</div><div className="metric-note">{forwardPeNote(data.market)}</div></div>
      </section>

      <MetricTrendPanel title="Revenue" rows={data.revenue} color="#fafafa" />
      <MetricTrendPanel title="Net income" rows={data.net_income} color="#d4d4d8" />
      {epsRows.length > 0
        ? <MetricTrendPanel title="EPS" rows={epsRows} color="#7dd3fc" valueFormatter={dollarsPerShare} note={epsRows.some(row => row.source === "derived from annual EPS") ? "Q4 is derived from annual EPS less reported Q1–Q3; TTM sums four quarterly EPS values." : "TTM sums the latest four quarterly EPS values."} />
        : <section className="section trend-panel"><header className="trend-panel-header"><h2 className="trend-panel-title">EPS trends</h2></header><p className="empty-trend-note">Quarterly EPS facts are not available for this ticker.</p></section>}

      <section className="section"><div className="section-title">Quarterly financials</div><div className="table-wrap"><table><thead><tr><th>Fiscal period</th><th>Period end</th><th>Report date</th><th>Revenue</th><th>Revenue YoY</th><th>Net income</th><th>Net income YoY</th></tr></thead><tbody>{table.map(row => <tr key={row.end}><td>{row.fiscal ?? "—"}</td><td>{row.end}</td><td>{row.revenue?.filed ?? row.income?.filed ?? "—"}</td><td>{money(row.revenue?.value)}</td><td>{pct(row.revenue?.yoy_pct)}</td><td>{money(row.income?.value)}</td><td>{pct(row.income?.yoy_pct)}</td></tr>)}</tbody></table></div></section>
      <div className="status">SEC concepts: revenue <strong>{data.revenue_concept}</strong> • net income <strong>{data.income_concept}</strong> • EPS <strong>{data.eps_concept ?? "unavailable"}</strong> • CIK {data.company.cik}. {data.cache.sec}.</div>
    </>}
  </main>;
}
