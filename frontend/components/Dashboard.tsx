"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { Bar, BarChart, CartesianGrid, Legend, Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

type Metric = {
  period_end: string; start_date?: string; fiscal_period?: string; filed?: string;
  value: number; yoy_pct: number | null; qoq_pct?: number | null;
  ttm_value?: number | null; ttm_yoy_pct?: number | null; ttm_qoq_pct?: number | null;
  source?: string; concept?: string;
};
type PEPoint = {
  period_end: string; fiscal_period?: string; price: number; ttm_eps: number; pe: number;
};
type TranscriptQuarter = { value: string; label: string };
type TranscriptSegment = { speaker: string; title: string; content: string; sentiment: string };
type EarningsTranscript = {
  ticker: string; quarter: string; fiscal_date_ending: string | null;
  reported_date: string | null; segments: TranscriptSegment[];
};
type BalanceSheetPoint = {
  period_end: string; fiscal_period: string; filed?: string | null;
  assets: number; liabilities: number; equity: number;
};
type LiquidityDebtPoint = {
  period_end: string; fiscal_period: string; filed?: string | null;
  cash: number | null; marketable_securities: number | null; debt: number | null;
  cash_qoq_pct: number | null; cash_yoy_pct: number | null;
  marketable_securities_qoq_pct: number | null; marketable_securities_yoy_pct: number | null;
  debt_qoq_pct: number | null; debt_yoy_pct: number | null;
};
type SharesOutstandingPoint = {
  period_end: string; fiscal_period: string; filed?: string | null;
  value: number; yoy_pct: number | null; qoq_pct?: number | null; concept?: string;
};
type MarginPoint = {
  period_end: string; fiscal_period: string;
  net_margin: number | null; gross_margin: number | null;
  net_margin_ttm: number | null; gross_margin_ttm: number | null;
  net_margin_qoq_pp: number | null; net_margin_yoy_pp: number | null;
  gross_margin_qoq_pp: number | null; gross_margin_yoy_pp: number | null;
  net_margin_ttm_qoq_pp: number | null; net_margin_ttm_yoy_pp: number | null;
  gross_margin_ttm_qoq_pp: number | null; gross_margin_ttm_yoy_pp: number | null;
};
type DashboardData = {
  company: { ticker: string; cik: number; name: string; exchange: string };
  market: {
    market_price: number | null; market_change: number | null;
    market_change_percent: number | null; market_cap: number | null;
    trailing_pe: number | null; forward_pe: number | null;
    earnings_date: string | null; earnings_date_type: "next" | "last" | null;
    forward_pe_status: "available" | "not_meaningful" | "unavailable";
    forward_eps: number | null; forward_period_end: string | null;
    forward_pe_analysts: number | null; market_source: string | null;
    fetched_at: string | null;
  };
  revenue_concept: string; income_concept: string;
  gross_profit_concept?: string; gross_profit?: Metric[]; margins?: MarginPoint[];
  eps_concept?: string; revenue: Metric[]; net_income: Metric[]; eps?: Metric[];
  free_cash_flow_concept?: string; free_cash_flow?: Metric[];
  balance_sheet_concept?: string; balance_sheet?: BalanceSheetPoint[];
  liquidity_debt_concept?: string; liquidity_debt?: LiquidityDebtPoint[];
  shares_outstanding_concept?: string; shares_outstanding?: SharesOutstandingPoint[];
  pe_history?: PEPoint[];
  quarterly_filings?: { period_end: string; filed: string; form: string; url: string }[];
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
function shares(value: number | null | undefined) {
  if (value == null || Number.isNaN(value)) return "—";
  const absolute = Math.abs(value);
  const sign = value < 0 ? "-" : "";
  if (absolute >= 1e12) return `${sign}${(absolute / 1e12).toFixed(2)}T`;
  if (absolute >= 1e9) return `${sign}${(absolute / 1e9).toFixed(2)}B`;
  if (absolute >= 1e6) return `${sign}${(absolute / 1e6).toFixed(2)}M`;
  if (absolute >= 1e3) return `${sign}${(absolute / 1e3).toFixed(1)}K`;
  return `${sign}${absolute.toLocaleString(undefined, { maximumFractionDigits: 0 })}`;
}
function dollarsPerShare(value: number | null | undefined) {
  return value == null || Number.isNaN(value) ? "—" : `$${value.toFixed(2)}`;
}
function pct(value: number | null | undefined) { return value == null ? "—" : `${value.toFixed(1)}%`; }
function signedMoney(value: number | null | undefined) {
  if (value == null || Number.isNaN(value)) return "—";
  const sign = value > 0 ? "+" : value < 0 ? "−" : "";
  return `${sign}$${Math.abs(value).toFixed(2)}`;
}
function signedPct(value: number | null | undefined) {
  if (value == null || Number.isNaN(value)) return "—";
  const sign = value > 0 ? "+" : value < 0 ? "−" : "";
  return `${sign}${Math.abs(value).toFixed(2)}%`;
}
function signedFinancialMoney(value: number | null | undefined) {
  if (value == null || Number.isNaN(value)) return "—";
  const sign = value > 0 ? "+" : value < 0 ? "−" : "";
  return `${sign}${money(Math.abs(value))}`;
}
function yearOverYearChange(rows: Metric[]) {
  const current = rows.at(-1);
  const prior = rows.at(-5);
  if (!current || !prior || current.yoy_pct == null || prior.value === 0) return null;
  const amount = current.value - prior.value;
  return { amount, percent: amount / Math.abs(prior.value) * 100 };
}
function displayDateTime(value: string) {
  return new Intl.DateTimeFormat(undefined, {
    month: "short", day: "numeric", year: "numeric",
    hour: "numeric", minute: "2-digit", timeZoneName: "short",
  }).format(new Date(value));
}
function quarterLabel(date: string) { const d = new Date(`${date}T00:00:00Z`); return `${d.getUTCFullYear()} Q${Math.floor(d.getUTCMonth()/3)+1}`; }
function fiscalShortLabel(fiscalPeriod: string | undefined, periodEnd: string) {
  const fiscal = fiscalPeriod?.match(/^(\d{4})\s+(Q\d)$/);
  if (fiscal) return `${fiscal[2]} ’${fiscal[1].slice(-2)}`;
  const calendar = quarterLabel(periodEnd).match(/^(\d{4})\s+(Q\d)$/);
  return calendar ? `${calendar[2]} ’${calendar[1].slice(-2)}` : periodEnd;
}
function shortPeriodLabel(row: Metric) {
  return fiscalShortLabel(row.fiscal_period, row.period_end);
}
function transcriptQuarter(value: string | undefined) {
  const match = value?.match(/^(\d{4})\s+(Q[1-4])$/);
  return match ? `${match[1]}${match[2]}` : null;
}

function OverviewChange({ amount, percent }: { amount: number | null; percent: number | null }) {
  const direction = amount == null || amount === 0 ? "flat" : amount > 0 ? "up" : "down";
  const arrow = direction === "up" ? "↑" : direction === "down" ? "↓" : "→";
  return <div className={`overview-change ${direction}`} aria-label={amount == null || percent == null
    ? "Year-over-year change unavailable"
    : `Year-over-year change ${signedFinancialMoney(amount)} ${signedPct(percent)}`}>
    <span className="overview-change-arrow" aria-hidden="true">{arrow}</span>
    <span className="overview-change-values">{amount == null || percent == null
      ? "—"
      : `${signedFinancialMoney(amount)} (${signedPct(percent)})`}</span>
  </div>;
}

function QuarterlyOverviewCard({ title, row, change }: {
  title: string; row: Metric | undefined;
  change: { amount: number; percent: number } | null;
}) {
  return <div className="card overview-card">
    <div className="metric-label">{title}</div>
    <div className="metric-value">{money(row?.value)}</div>
    <OverviewChange amount={change?.amount ?? null} percent={change?.percent ?? null} />
    <div className="metric-note">{row ? `${shortPeriodLabel(row)} · YoY change` : "Latest quarter unavailable"}</div>
  </div>;
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
    <header className="trend-panel-header"><div><h2 className="trend-panel-title">{title}</h2>{note && <p className="trend-panel-note">{note}</p>}</div>{amountToggle}</header>
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

function MarginTrendPanel({ rows }: { rows: MarginPoint[] }) {
  const [amountMode, setAmountMode] = useState<"quarterly" | "ttm">("quarterly");
  const [growthMode, setGrowthMode] = useState<"yoy" | "qoq">("yoy");
  const chartData = useMemo(() => rows.map(row => ({
    label: fiscalShortLabel(row.fiscal_period, row.period_end),
    netMargin: amountMode === "quarterly" ? row.net_margin : row.net_margin_ttm,
    grossMargin: amountMode === "quarterly" ? row.gross_margin : row.gross_margin_ttm,
    netChange: amountMode === "quarterly"
      ? (growthMode === "qoq" ? row.net_margin_qoq_pp : row.net_margin_yoy_pp)
      : (growthMode === "qoq" ? row.net_margin_ttm_qoq_pp : row.net_margin_ttm_yoy_pp),
    grossChange: amountMode === "quarterly"
      ? (growthMode === "qoq" ? row.gross_margin_qoq_pp : row.gross_margin_yoy_pp)
      : (growthMode === "qoq" ? row.gross_margin_ttm_qoq_pp : row.gross_margin_ttm_yoy_pp),
  })), [rows, amountMode, growthMode]);
  const amountLabel = amountMode === "quarterly" ? "Quarterly net and gross margin" : "Net and gross margin TTM";
  const changeLabel = `${amountMode === "quarterly" ? "Quarterly" : "TTM"} margin ${growthMode.toUpperCase()} change`;
  const hasGrossMargin = chartData.some(point => point.grossMargin != null);

  const amountToggle = <div className="segmented-group" role="group" aria-label="Margin amount period">
    <span className="control-label">Amount</span>
    <div className="segmented-control">
      <button type="button" aria-pressed={amountMode === "quarterly"} onClick={() => setAmountMode("quarterly")}>Quarterly</button>
      <button type="button" aria-pressed={amountMode === "ttm"} onClick={() => setAmountMode("ttm")}>TTM</button>
    </div>
  </div>;
  const growthToggle = <div className="segmented-group" role="group" aria-label="Margin change period">
    <span className="control-label">Change</span>
    <div className="segmented-control">
      <button type="button" aria-pressed={growthMode === "qoq"} onClick={() => setGrowthMode("qoq")}>QoQ</button>
      <button type="button" aria-pressed={growthMode === "yoy"} onClick={() => setGrowthMode("yoy")}>YoY</button>
    </div>
  </div>;

  return <section className="section trend-panel">
    <header className="trend-panel-header">
      <div>
        <h2 className="trend-panel-title">Net and gross margin</h2>
        <p className="trend-panel-note">Net margin is net income divided by revenue and gross margin is gross profit divided by revenue.</p>
      </div>
      {amountToggle}
    </header>
    <div className="trend-chart-stack">
      <div className="card trend-chart-card">
        <header className="trend-chart-header"><h3 className="section-title">{amountLabel}</h3></header>
        <div className="trend-chart-wrap">{chartData.length > 0
          ? <ResponsiveContainer width="100%" height="100%"><LineChart data={chartData} margin={{ top: 4, right: 8, left: 4, bottom: 8 }}>
              <CartesianGrid stroke="#27272a" strokeDasharray="3 3" />
              <XAxis dataKey="label" tick={{ fill: "#a1a1aa", fontSize: 11 }} interval="preserveStartEnd" />
              <YAxis tick={{ fill: "#a1a1aa", fontSize: 11 }} tickFormatter={value => `${Number(value).toFixed(0)}%`} width={56} />
              <Tooltip contentStyle={{ background: "#18181b", border: "1px solid #3f3f46", borderRadius: 8 }} formatter={(value, name) => [value == null ? "—" : `${Number(value).toFixed(1)}%`, name]} />
              <Legend wrapperStyle={{ color: "#a1a1aa", fontSize: 12 }} />
              <ReferenceLine y={0} stroke="#52525b" />
              <Line type="monotone" dataKey="netMargin" name="Net margin" stroke="#60a5fa" strokeWidth={2} dot={false} activeDot={{ r: 4 }} connectNulls={false} />
              <Line type="monotone" dataKey="grossMargin" name="Gross margin" stroke="#4ade80" strokeWidth={2} dot={false} activeDot={{ r: 4 }} connectNulls={false} />
            </LineChart></ResponsiveContainer>
          : <div className="empty-trend-note">Quarterly margins are not available for this ticker.</div>}
        </div>
        {chartData.length > 0 && !hasGrossMargin && <p className="trend-panel-note">Gross profit facts are unavailable; net margin is shown where possible.</p>}
      </div>
      <div className="card trend-chart-card">
        <header className="trend-chart-header"><h3 className="section-title">{changeLabel} (percentage points)</h3>{growthToggle}</header>
        <div className="trend-chart-wrap">{chartData.length > 0
          ? <ResponsiveContainer width="100%" height="100%"><BarChart data={chartData} margin={{ top: 4, right: 8, left: 2, bottom: 8 }}>
              <CartesianGrid stroke="#27272a" strokeDasharray="3 3" />
              <XAxis dataKey="label" tick={{ fill: "#a1a1aa", fontSize: 11 }} interval="preserveStartEnd" />
              <YAxis tick={{ fill: "#a1a1aa", fontSize: 11 }} tickFormatter={value => `${Number(value).toFixed(0)} pp`} width={58} />
              <Tooltip contentStyle={{ background: "#18181b", border: "1px solid #3f3f46", borderRadius: 8 }} formatter={(value, name) => [value == null ? "—" : `${Number(value).toFixed(1)} pp`, name]} />
              <Legend wrapperStyle={{ color: "#a1a1aa", fontSize: 12 }} />
              <ReferenceLine y={0} stroke="#71717a" />
              <Bar dataKey="netChange" name="Net margin" fill="#60a5fa" radius={[4, 4, 0, 0]} maxBarSize={52} />
              <Bar dataKey="grossChange" name="Gross margin" fill="#4ade80" radius={[4, 4, 0, 0]} maxBarSize={52} />
            </BarChart></ResponsiveContainer>
          : <div className="empty-trend-note">Quarterly margin changes are not available for this ticker.</div>}
        </div>
      </div>
    </div>
  </section>;
}

function BalanceSheetPanel({ rows }: { rows: BalanceSheetPoint[] }) {
  const chartData = useMemo(() => rows.map(row => ({
    ...row,
    label: fiscalShortLabel(row.fiscal_period, row.period_end),
  })), [rows]);

  return <section className="section trend-panel">
    <header className="trend-panel-header">
      <div>
        <h2 className="trend-panel-title">Assets</h2>
        <p className="trend-panel-note">Assets are resources the company owns, liabilities are amounts it owes, and equity is assets minus liabilities.</p>
      </div>
    </header>
    <div className="card trend-chart-card">
      <header className="trend-chart-header"><h3 className="section-title">Quarterly balance sheet</h3></header>
      <div className="trend-chart-wrap">{chartData.length > 0
        ? <ResponsiveContainer width="100%" height="100%"><BarChart data={chartData} margin={{ top: 4, right: 8, left: 4, bottom: 8 }}>
            <CartesianGrid stroke="#27272a" strokeDasharray="3 3" />
            <XAxis dataKey="label" tick={{ fill: "#a1a1aa", fontSize: 11 }} interval="preserveStartEnd" />
            <YAxis tick={{ fill: "#a1a1aa", fontSize: 11 }} tickFormatter={v => money(Number(v))} width={86} />
            <Tooltip
              cursor={{ fill: "#27272a", fillOpacity: 0.22 }}
              content={({ active, payload }) => {
                const point = payload?.[0]?.payload as BalanceSheetPoint | undefined;
                if (!active || !point) return null;
                const share = (value: number) => point.assets === 0 ? "—" : pct(value / point.assets * 100);
                return <div className="chart-tooltip">
                  <strong>{point.fiscal_period} · {point.period_end}</strong>
                  <div><span>Assets</span><b>{money(point.assets)}</b></div>
                  <div><span>Equity</span><b>{money(point.equity)} ({share(point.equity)})</b></div>
                  <div><span>Liabilities</span><b>{money(point.liabilities)} ({share(point.liabilities)})</b></div>
                </div>;
              }}
            />
            <Legend formatter={value => value === "liabilities" ? "Liabilities" : "Equity"} wrapperStyle={{ color: "#a1a1aa", fontSize: 12 }} />
            <ReferenceLine y={0} stroke="#52525b" />
            <Bar dataKey="liabilities" stackId="balance" fill="#f97316" radius={[0, 0, 0, 0]} maxBarSize={52} />
            <Bar dataKey="equity" stackId="balance" fill="#60a5fa" radius={[4, 4, 0, 0]} maxBarSize={52} />
          </BarChart></ResponsiveContainer>
        : <div className="empty-trend-note">Quarterly assets and liabilities are not available in SEC facts for this ticker.</div>}
      </div>
    </div>
  </section>;
}

function LiquidityDebtPanel({ rows }: { rows: LiquidityDebtPoint[] }) {
  const [growthMode, setGrowthMode] = useState<"qoq" | "yoy">("yoy");
  const chartData = useMemo(() => rows.map(row => ({
    ...row,
    label: fiscalShortLabel(row.fiscal_period, row.period_end),
    cash_change: growthMode === "qoq" ? row.cash_qoq_pct : row.cash_yoy_pct,
    marketable_securities_change: growthMode === "qoq"
      ? row.marketable_securities_qoq_pct : row.marketable_securities_yoy_pct,
    debt_change: growthMode === "qoq" ? row.debt_qoq_pct : row.debt_yoy_pct,
  })), [rows, growthMode]);

  return <section className="section trend-panel">
    <header className="trend-panel-header">
      <div>
        <h2 className="trend-panel-title">Cash, marketable securities &amp; debt</h2>
        <p className="trend-panel-note">Cash and marketable securities are liquid assets, while debt is borrowed funds.</p>
      </div>
    </header>
    <div className="trend-chart-stack">
      <div className="card trend-chart-card">
        <header className="trend-chart-header"><h3 className="section-title">Quarterly cash, marketable securities &amp; debt</h3></header>
        <div className="trend-chart-wrap">{chartData.length > 0
          ? <ResponsiveContainer width="100%" height="100%"><BarChart data={chartData} margin={{ top: 4, right: 8, left: 4, bottom: 8 }}>
              <CartesianGrid stroke="#27272a" strokeDasharray="3 3" />
              <XAxis dataKey="label" tick={{ fill: "#a1a1aa", fontSize: 11 }} interval="preserveStartEnd" />
              <YAxis tick={{ fill: "#a1a1aa", fontSize: 11 }} tickFormatter={v => money(Number(v))} width={86} />
              <Tooltip
                cursor={{ fill: "#27272a", fillOpacity: 0.22 }}
                content={({ active, payload }) => {
                  const point = payload?.[0]?.payload as LiquidityDebtPoint | undefined;
                  if (!active || !point) return null;
                  return <div className="chart-tooltip">
                    <strong>{point.fiscal_period} · {point.period_end}</strong>
                    <div><span>Cash</span><b>{money(point.cash)}</b></div>
                    <div><span>Marketable securities</span><b>{money(point.marketable_securities)}</b></div>
                    <div><span>Cash + securities</span><b>{money(
                      point.cash == null || point.marketable_securities == null
                        ? null : point.cash + point.marketable_securities,
                    )}</b></div>
                    <div><span>Debt</span><b>{money(point.debt)}</b></div>
                  </div>;
                }}
              />
              <Legend formatter={value => value === "cash" ? "Cash" : value === "marketable_securities" ? "Marketable securities" : "Debt"} wrapperStyle={{ color: "#a1a1aa", fontSize: 12 }} />
              <ReferenceLine y={0} stroke="#52525b" />
              <Bar dataKey="cash" stackId="liquidity" name="cash" fill="#4ade80" maxBarSize={52} />
              <Bar dataKey="marketable_securities" stackId="liquidity" name="marketable_securities" fill="#60a5fa" radius={[4, 4, 0, 0]} maxBarSize={52} />
              <Bar dataKey="debt" name="debt" fill="#f97316" radius={[4, 4, 0, 0]} maxBarSize={52} />
            </BarChart></ResponsiveContainer>
          : <div className="empty-trend-note">Quarterly cash, marketable securities, and debt are not available in SEC facts for this ticker.</div>}
        </div>
      </div>
      <div className="card trend-chart-card">
        <header className="trend-chart-header">
          <h3 className="section-title">Cash, marketable securities &amp; debt {growthMode.toUpperCase()} change</h3>
          <div className="segmented-group" role="group" aria-label="Liquidity and debt change period">
            <span className="control-label">Change</span>
            <div className="segmented-control">
              <button type="button" aria-pressed={growthMode === "qoq"} onClick={() => setGrowthMode("qoq")}>QoQ</button>
              <button type="button" aria-pressed={growthMode === "yoy"} onClick={() => setGrowthMode("yoy")}>YoY</button>
            </div>
          </div>
        </header>
        <div className="trend-chart-wrap">{chartData.length > 0
          ? <ResponsiveContainer width="100%" height="100%"><BarChart data={chartData} margin={{ top: 4, right: 8, left: 2, bottom: 8 }}>
              <CartesianGrid stroke="#27272a" strokeDasharray="3 3" />
              <XAxis dataKey="label" tick={{ fill: "#a1a1aa", fontSize: 11 }} interval="preserveStartEnd" />
              <YAxis tick={{ fill: "#a1a1aa", fontSize: 11 }} tickFormatter={v => `${Number(v).toFixed(0)}%`} width={54} />
              <Tooltip contentStyle={{ background: "#18181b", border: "1px solid #3f3f46", borderRadius: 8 }} formatter={v => [v == null ? "—" : `${Number(v).toFixed(1)}%`, growthMode.toUpperCase()]} />
              <Legend formatter={value => value === "cash_change" ? "Cash" : value === "marketable_securities_change" ? "Marketable securities" : "Debt"} wrapperStyle={{ color: "#a1a1aa", fontSize: 12 }} />
              <ReferenceLine y={0} stroke="#71717a" />
              <Bar dataKey="cash_change" name="cash_change" fill="#4ade80" radius={[4, 4, 0, 0]} maxBarSize={52} />
              <Bar dataKey="marketable_securities_change" name="marketable_securities_change" fill="#60a5fa" radius={[4, 4, 0, 0]} maxBarSize={52} />
              <Bar dataKey="debt_change" name="debt_change" fill="#f97316" radius={[4, 4, 0, 0]} maxBarSize={52} />
            </BarChart></ResponsiveContainer>
          : <div className="empty-trend-note">Quarterly balance changes are not available in SEC facts for this ticker.</div>}
        </div>
      </div>
    </div>
  </section>;
}

function SharesOutstandingPanel({ rows, concept }: { rows: SharesOutstandingPoint[]; concept: string }) {
  const [growthMode, setGrowthMode] = useState<"yoy" | "qoq">("yoy");
  const usesWeightedAverage = concept.toLowerCase().includes("weighted average");
  const seriesLabel = usesWeightedAverage
    ? "Quarterly weighted-average shares"
    : "Quarter-end shares outstanding";
  const note = usesWeightedAverage
    ? "Shares outstanding are common shares held by investors; because point-in-time shares are not tagged in this company's SEC facts, the chart uses quarterly weighted-average basic shares, with year-over-year (YoY)/quarter-over-quarter (QoQ) comparing the same quarter last year or prior quarter."
    : "Shares outstanding are common shares held by investors at quarter end.";
  const chartData = useMemo(() => rows.map(row => ({
    ...row,
    label: fiscalShortLabel(row.fiscal_period, row.period_end),
    growth: growthMode === "yoy" ? row.yoy_pct : row.qoq_pct ?? null,
  })), [rows, growthMode]);

  return <section className="section trend-panel">
    <header className="trend-panel-header">
      <div>
        <h2 className="trend-panel-title">Shares outstanding</h2>
        <p className="trend-panel-note">{note}</p>
      </div>
    </header>
    <div className="trend-chart-stack">
      <div className="card trend-chart-card">
        <header className="trend-chart-header"><h3 className="section-title">{seriesLabel}</h3></header>
        <div className="trend-chart-wrap">{chartData.length > 0
          ? <ResponsiveContainer width="100%" height="100%"><BarChart data={chartData} margin={{ top: 4, right: 8, left: 4, bottom: 8 }}>
              <CartesianGrid stroke="#27272a" strokeDasharray="3 3" />
              <XAxis dataKey="label" tick={{ fill: "#a1a1aa", fontSize: 11 }} interval="preserveStartEnd" />
              <YAxis tick={{ fill: "#a1a1aa", fontSize: 11 }} tickFormatter={v => shares(Number(v))} width={86} />
              <Tooltip contentStyle={{ background: "#18181b", border: "1px solid #3f3f46", borderRadius: 8 }} formatter={v => [v == null ? "—" : shares(Number(v)), seriesLabel]} />
              <ReferenceLine y={0} stroke="#52525b" />
              <Bar dataKey="value" fill="#c4b5fd" radius={[4, 4, 0, 0]} maxBarSize={52} />
            </BarChart></ResponsiveContainer>
          : <div className="empty-trend-note">Share-count facts are not available in SEC facts for this ticker.</div>}
        </div>
      </div>
      <div className="card trend-chart-card">
        <header className="trend-chart-header">
          <h3 className="section-title">{usesWeightedAverage ? "Weighted-average shares" : "Shares outstanding"} {growthMode.toUpperCase()} change</h3>
          <div className="segmented-group" role="group" aria-label="Shares outstanding growth comparison">
            <span className="control-label">Change</span>
            <div className="segmented-control">
              <button type="button" aria-pressed={growthMode === "yoy"} onClick={() => setGrowthMode("yoy")}>YoY</button>
              <button type="button" aria-pressed={growthMode === "qoq"} onClick={() => setGrowthMode("qoq")}>QoQ</button>
            </div>
          </div>
        </header>
        <div className="trend-chart-wrap">{chartData.length > 0
          ? <ResponsiveContainer width="100%" height="100%"><BarChart data={chartData} margin={{ top: 4, right: 8, left: 2, bottom: 8 }}>
              <CartesianGrid stroke="#27272a" strokeDasharray="3 3" />
              <XAxis dataKey="label" tick={{ fill: "#a1a1aa", fontSize: 11 }} interval="preserveStartEnd" />
              <YAxis tick={{ fill: "#a1a1aa", fontSize: 11 }} tickFormatter={v => `${v}%`} width={54} />
              <Tooltip contentStyle={{ background: "#18181b", border: "1px solid #3f3f46", borderRadius: 8 }} formatter={v => [v == null ? "—" : `${Number(v).toFixed(1)}%`, `${growthMode.toUpperCase()} change`]} />
              <ReferenceLine y={0} stroke="#71717a" />
              <Bar dataKey="growth" fill="#c4b5fd" radius={[4, 4, 0, 0]} maxBarSize={52} />
            </BarChart></ResponsiveContainer>
          : <div className="empty-trend-note">Share-count change is unavailable for this ticker.</div>}
        </div>
      </div>
    </div>
  </section>;
}

function PETrendPanel({ rows, market }: { rows: PEPoint[]; market: DashboardData["market"] }) {
  const [mode, setMode] = useState<"forward" | "trailing">("forward");
  const forwardPe = market.forward_pe_status === "available" ? market.forward_pe : null;
  const chartData = useMemo(() => mode === "forward"
    ? (forwardPe == null ? [] : [{ label: "Current", pe: forwardPe }])
    : rows.map(row => ({ label: fiscalShortLabel(row.fiscal_period, row.period_end), pe: row.pe })),
  [mode, forwardPe, rows]);
  const chartTitle = mode === "forward" ? "Forward P/E" : "Trailing P/E";
  const note = "Price-to-earnings (P/E) compares a share price with earnings per share (EPS).";
  const emptyMessage = mode === "forward"
    ? market.forward_pe_status === "not_meaningful"
      ? "Forward P/E is not meaningful because forecast EPS is nonpositive."
      : "Yahoo Finance did not provide a forward estimate."
    : "Historical P/E is unavailable for the selected history.";

  return <section className="section trend-panel">
    <header className="trend-panel-header">
      <div>
        <h2 className="trend-panel-title">P/E</h2>
        <p className="trend-panel-note">{note}</p>
      </div>
      <div className="segmented-control" role="group" aria-label="P/E ratio type">
        <button type="button" aria-pressed={mode === "forward"} onClick={() => setMode("forward")}>Forward</button>
        <button type="button" aria-pressed={mode === "trailing"} onClick={() => setMode("trailing")}>Trailing</button>
      </div>
    </header>
    <div className="card trend-chart-card">
      <header className="trend-chart-header"><h3 className="section-title">{chartTitle}</h3></header>
      <div className="trend-chart-wrap">{chartData.length > 0
        ? <ResponsiveContainer width="100%" height="100%"><BarChart data={chartData} margin={{ top: 4, right: 8, left: 4, bottom: 8 }}>
            <CartesianGrid stroke="#27272a" strokeDasharray="3 3" />
            <XAxis dataKey="label" tick={{ fill: "#a1a1aa", fontSize: 11 }} interval="preserveStartEnd" />
            <YAxis tick={{ fill: "#a1a1aa", fontSize: 11 }} tickFormatter={v => `${Number(v).toFixed(0)}x`} width={54} />
            <Tooltip contentStyle={{ background: "#18181b", border: "1px solid #3f3f46", borderRadius: 8 }} formatter={v => [`${Number(v).toFixed(2)}x`, chartTitle]} />
            <ReferenceLine y={0} stroke="#71717a" />
            <Bar dataKey="pe" fill="#fbbf24" radius={[4, 4, 0, 0]} maxBarSize={52} />
          </BarChart></ResponsiveContainer>
        : <div className="empty-trend-note">{emptyMessage}</div>}
      </div>
    </div>
  </section>;
}

function EarningsCallPanel({ ticker, quarters }: { ticker: string; quarters: TranscriptQuarter[] }) {
  const [quarter, setQuarter] = useState(quarters[0]?.value ?? "");
  const [transcript, setTranscript] = useState<EarningsTranscript | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!quarters.some(item => item.value === quarter)) {
      setQuarter(quarters[0]?.value ?? "");
      setTranscript(null);
    }
  }, [quarters, quarter]);

  async function fetchTranscript() {
    if (!quarter || loading) return;
    setLoading(true);
    setError("");
    setTranscript(null);
    try {
      const response = await fetch("/api/earnings-transcript", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ticker, quarter }),
      });
      const body = await response.json();
      if (!response.ok) throw new Error(body.detail || "Could not fetch the earnings call transcript.");
      setTranscript(body as EarningsTranscript);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not fetch the earnings call transcript.");
    } finally {
      setLoading(false);
    }
  }

  return <section className="section earnings-call-panel">
    <header className="earnings-call-header">
      <div>
        <h2 className="trend-panel-title">Earnings Call Transcript</h2>
        <p className="trend-panel-note">Press Fetch transcript to request it from Alpha Vantage; successful transcripts are saved locally and reused for this ticker and quarter.</p>
      </div>
      <div className="earnings-call-controls">
        <label className="sr-only" htmlFor="transcript-quarter">Fiscal quarter</label>
        <select id="transcript-quarter" className="select" value={quarter} onChange={event => { setQuarter(event.target.value); setTranscript(null); setError(""); }} disabled={quarters.length === 0 || loading}>
          {quarters.length === 0 ? <option value="">No quarters available</option> : quarters.map(item => <option key={item.value} value={item.value}>{item.label}</option>)}
        </select>
        <button className="button" type="button" onClick={() => void fetchTranscript()} disabled={!quarter || loading}>
          {loading ? "Fetching transcript…" : "Fetch transcript"}
        </button>
      </div>
    </header>
    {error && <div className="error transcript-error" role="alert">{error}</div>}
    {transcript && <div className="transcript-results">
      <div className="transcript-meta">
        <strong>{transcript.ticker} · {transcript.quarter}</strong>
        {transcript.reported_date && <span>Reported {transcript.reported_date}</span>}
        {transcript.fiscal_date_ending && <span>Period ended {transcript.fiscal_date_ending}</span>}
      </div>
      {transcript.segments.length > 0
        ? <div className="transcript-turns">{transcript.segments.map((segment, index) => <article className="transcript-turn" key={`${index}-${segment.speaker}`}>
            {(segment.speaker || segment.title || segment.sentiment) && <header className="transcript-speaker">
              <div>{segment.speaker && <strong>{segment.speaker}</strong>}{segment.title && <span>{segment.title}</span>}</div>
              {segment.sentiment && <span className="transcript-sentiment">{segment.sentiment}</span>}
            </header>}
            <p>{segment.content}</p>
          </article>)}</div>
        : <p className="empty-trend-note">Alpha Vantage returned no transcript for this quarter.</p>}
    </div>}
    {!transcript && !error && !loading && <p className="empty-trend-note">No transcript has been fetched yet. The API is contacted only when you press “Fetch transcript.”</p>}
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
  const [years, setYears] = useState("4");
    const [activeTab, setActiveTab] = useState<"financials" | "earnings">("financials");
  const [data, setData] = useState<DashboardData | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [openingFiling, setOpeningFiling] = useState(false);
  const [filingMessage, setFilingMessage] = useState("");
  const requestId = useRef(0);

  async function load(symbol = ticker, history = years) {
    const currentRequest = ++requestId.current;
    setLoading(true); setError(""); setFilingMessage("");
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
  async function openFilingPdf(url: string) {
    setOpeningFiling(true);
    setFilingMessage("");
    try {
      const response = await fetch(`/api/filing-pdf?url=${encodeURIComponent(url)}`, { cache: "no-store" });
      const body = await response.json();
      if (!response.ok) throw new Error(body.detail || "Could not open the filing PDF.");
    } catch (e) {
      setFilingMessage(e instanceof Error ? e.message : "Could not open the filing PDF.");
    } finally {
      setOpeningFiling(false);
    }
  }
  useEffect(() => { void load(); }, []);

  const table = useMemo(() => {
    const map = new Map<string, { end:string; fiscal?:string; revenue?:Metric; income?:Metric }>();
    for (const row of data?.revenue ?? []) map.set(row.period_end, { ...(map.get(row.period_end) ?? { end: row.period_end }), revenue: row, fiscal: row.fiscal_period });
    for (const row of data?.net_income ?? []) map.set(row.period_end, { ...(map.get(row.period_end) ?? { end: row.period_end }), income: row, fiscal: map.get(row.period_end)?.fiscal ?? row.fiscal_period });
    return [...map.values()].sort((a,b) => b.end.localeCompare(a.end));
  }, [data]);
  const quarterlyFilings = useMemo(
    () => new Map((data?.quarterly_filings ?? []).map(filing => [filing.period_end, filing])),
    [data?.quarterly_filings],
  );
  const earningsQuarters = useMemo(() => table.flatMap(row => {
    const label = row.fiscal ?? quarterLabel(row.end);
    const value = transcriptQuarter(label);
    return value ? [{ value, label }] : [];
  }), [table]);
  const latest = data?.revenue.at(-1);
  const latestIncome = data?.net_income.at(-1);
  const revenueChange = data ? yearOverYearChange(data.revenue) : null;
  const incomeChange = data ? yearOverYearChange(data.net_income) : null;
  const epsRows = data?.eps ?? [];
  const freeCashFlowRows = data?.free_cash_flow ?? [];
  const balanceSheetRows = data?.balance_sheet ?? [];
  const liquidityDebtRows = data?.liquidity_debt ?? [];
  const sharesOutstandingRows = data?.shares_outstanding ?? [];
  const peHistory = data?.pe_history ?? [];
  const latestShares = sharesOutstandingRows.at(-1)?.value;
  const marketCapFromShares = data?.market.market_price != null && latestShares != null && latestShares > 0
    ? data.market.market_price * latestShares
    : null;
  const currentMarketCap = data?.market.market_cap ?? marketCapFromShares;
  const marketCapNote = data?.market.market_cap != null
    ? "Current market data"
    : marketCapFromShares != null ? "Price × latest SEC share count" : "Unavailable";
  const latestTtmEps = epsRows.at(-1)?.ttm_value ?? null;
  const trailingPeNotMeaningful = latestTtmEps != null
    ? latestTtmEps <= 0
    : data?.market.trailing_pe != null && data.market.trailing_pe <= 0;
  const trailingPe = latestTtmEps != null
    ? latestTtmEps > 0 && data?.market.market_price != null
      ? data.market.market_price / latestTtmEps
      : null
    : data?.market.trailing_pe ?? null;
  const trailingPeNote = latestTtmEps != null
    ? "Current price ÷ latest SEC TTM EPS"
    : "Yahoo Finance trailing P/E";
  const dailyChange = data?.market.market_change ?? null;
  const dailyChangePercent = data?.market.market_change_percent ?? null;
  const dailyDirectionValue = dailyChange ?? dailyChangePercent ?? 0;
  const dailyDirection = dailyDirectionValue > 0 ? "up" : dailyDirectionValue < 0 ? "down" : "flat";

  return <main className="container">
    <header className="header">
      <div><div className="eyebrow"></div><h1>SEC Financial Dashboard</h1><div className="subtitle">Quarterly fundamentals with a local cache. No cloud database required.</div></div>
      <div className="controls">
        <div className="field"><label>Ticker</label><input className="input" value={ticker} onChange={e => setTicker(e.target.value.toUpperCase())} onKeyDown={e => e.key === "Enter" && void load()} /></div>
        <div className="field"><label>History</label><select className="select" value={years} onChange={e => { const nextYears = e.target.value; setYears(nextYears); void load(ticker, nextYears); }}><option value="2">2 years</option><option value="3">3 years</option><option value="4">4 years</option><option value="5">5 years</option></select></div>
        <button className="button" disabled={loading} onClick={() => void load()}>{loading ? "Loading…" : "Refresh"}</button>
      </div>
    </header>

    {error && <div className="error">{error}</div>}
    {data && <>
      <div className="card company-banner">
        <div className="company-identity"><strong>{data.company.name}</strong><span className="badge">{data.company.ticker}</span><span className="badge">{data.company.exchange || "SEC"}</span></div>
        <div className="stock-quote">
          <div><div className="stock-price-label">Current stock price</div><div className="stock-price">{data.market.market_price == null ? "—" : `$${data.market.market_price.toFixed(2)}`}</div></div>
          <div className={`stock-day-change ${dailyDirection}`}>
            <span className={`stock-direction-arrow ${dailyDirection}`} role="img" aria-label={dailyDirection === "up" ? "Up" : dailyDirection === "down" ? "Down" : "No change"}>
              {dailyDirection === "up" ? "↑" : dailyDirection === "down" ? "↓" : "→"}
            </span>
            <span className="stock-day-values">{signedMoney(data.market.market_change)} ({signedPct(data.market.market_change_percent)})</span>
            <span className="stock-day-label">Past day</span>
          </div>
          <div className="earnings-date-note">
            {data.market.earnings_date && data.market.earnings_date_type
              ? `${data.market.earnings_date_type === "next" ? "Next" : "Last"} earnings date: ${displayDateTime(data.market.earnings_date)}`
              : "Earnings date unavailable"}
          </div>
        </div>
        <div className="stock-price-note">Market snapshot cached 15 min</div>
      </div>
      <div className="dashboard-tabs" role="tablist" aria-label="Dashboard sections">
              <button id="financials-tab" type="button" role="tab" aria-selected={activeTab === "financials"} aria-controls="financials-panel" onClick={() => setActiveTab("financials")}>Financials</button>
        <button id="earnings-tab" type="button" role="tab" aria-selected={activeTab === "earnings"} aria-controls="earnings-panel" onClick={() => setActiveTab("earnings")}>Earnings Call</button>
      </div>
          <div id="financials-panel" role="tabpanel" aria-labelledby="financials-tab" hidden={activeTab !== "financials"}>
      <section className="section overview-section">
        <h2 className="overview-title">Overview</h2>
        <div className="overview-grid">
          <div className="card overview-card">
            <div className="metric-label">Current market cap</div>
            <div className="metric-value">{money(currentMarketCap)}</div>
            <div className="metric-note">{marketCapNote}</div>
          </div>
          <QuarterlyOverviewCard title="Latest quarterly revenue" row={latest} change={revenueChange} />
          <QuarterlyOverviewCard title="Latest quarterly net income" row={latestIncome} change={incomeChange} />
          <div className="card overview-card">
            <div className="metric-label">TTM trailing P/E</div>
            <div className="metric-value">{trailingPeNotMeaningful ? "N/M" : trailingPe == null ? "—" : `${trailingPe.toFixed(2)}x`}</div>
            <div className="metric-note">{trailingPeNotMeaningful ? "TTM EPS is nonpositive" : trailingPeNote}</div>
          </div>
          <div className="card overview-card">
            <div className="metric-label">Forward P/E</div>
            <div className="metric-value">{data.market.forward_pe_status === "not_meaningful" ? "N/M" : data.market.forward_pe == null ? "—" : `${data.market.forward_pe.toFixed(2)}x`}</div>
            <div className="metric-note">{forwardPeNote(data.market)}</div>
          </div>
        </div>
      </section>

      <MetricTrendPanel title="Revenue" rows={data.revenue} color="#fafafa" note="Revenue is sales earned before expenses." />
      <MetricTrendPanel title="Net income" rows={data.net_income} color="#fafafa" note="Net income is profit after expenses and taxes." />
      <MarginTrendPanel rows={data.margins ?? []} />
      {freeCashFlowRows.length > 0
        ? <MetricTrendPanel title="Free cash flow" rows={freeCashFlowRows} color="#86efac" note="Free cash flow is operating cash flow minus capital expenditures from SEC cash flow statements." />
        : <section className="section trend-panel"><header className="trend-panel-header"><h2 className="trend-panel-title">Free cash flow</h2></header><p className="trend-panel-note">Free cash flow is operating cash flow minus capital expenditures; both figures must be reported for the same quarter.</p><p className="empty-trend-note">Free cash flow requires reported operating cash flow and capital expenditures for matching quarters.</p></section>}
      {epsRows.length > 0
        ? <MetricTrendPanel title="EPS" rows={epsRows} color="#fafafa" valueFormatter={dollarsPerShare} note={epsRows.some(row => row.source === "derived from annual EPS") ? "Earnings per share (EPS) is net income per share." : "Earnings per share (EPS) is net income per share."} />
        : <section className="section trend-panel"><header className="trend-panel-header"><h2 className="trend-panel-title">EPS</h2></header><p className="trend-panel-note">Earnings per share (EPS) is net income per share.</p><p className="empty-trend-note">Quarterly EPS facts are not available for this ticker.</p></section>}
      <BalanceSheetPanel rows={balanceSheetRows} />
      <LiquidityDebtPanel rows={liquidityDebtRows} />
      <SharesOutstandingPanel rows={sharesOutstandingRows} concept={data.shares_outstanding_concept ?? ""} />
      <PETrendPanel rows={peHistory} market={data.market} />

      <section className="section"><div className="section-title">Quarterly financials</div><div className="table-wrap"><table><thead><tr><th>Fiscal period</th><th>Period end</th><th>Report date</th><th>Revenue</th><th>Revenue YoY</th><th>Net income</th><th>Net income YoY</th><th>Documents</th></tr></thead><tbody>{table.map(row => {
        const filing = quarterlyFilings.get(row.end);
        const form = row.fiscal?.endsWith("Q4") ? "10-K" : "10-Q";
        const fiscalLabel = row.fiscal ?? quarterLabel(row.end);
        const secSearch = `https://www.sec.gov/edgar/search/#/q=${encodeURIComponent(`${form} ${row.end}`)}&ciks=${data.company.cik}&dateRange=all`;
        const slidesSearch = `https://www.google.com/search?q=${encodeURIComponent(`${data.company.ticker} ${fiscalLabel} earnings presentation slides`)}`;
        return <tr key={row.end}><td>{row.fiscal ?? "—"}</td><td>{row.end}</td><td>{row.revenue?.filed ?? row.income?.filed ?? "—"}</td><td>{money(row.revenue?.value)}</td><td>{pct(row.revenue?.yoy_pct)}</td><td>{money(row.income?.value)}</td><td>{pct(row.income?.yoy_pct)}</td><td className="quarter-actions-cell"><details name="quarter-documents" className="quarter-actions"><summary aria-label={`Open documents for ${fiscalLabel}`} title={`Open documents for ${fiscalLabel}`}>⋮</summary><div className="quarter-actions-menu">
          {filing
            ? <button type="button" disabled={openingFiling} onClick={() => void openFilingPdf(filing.url)}>{openingFiling ? "Opening PDF…" : `Open ${filing.form} PDF`}</button>
            : <a href={secSearch} target="_blank" rel="noreferrer">Search SEC {form} filings</a>}
          <a href={slidesSearch} target="_blank" rel="noreferrer">Search presentation slides</a>
          {filingMessage && <span className="quarter-actions-message" role="status">{filingMessage}</span>}
        </div></details></td></tr>;
      })}</tbody></table></div></section>
      <div className="status">SEC concepts: revenue <strong>{data.revenue_concept}</strong> • net income <strong>{data.income_concept}</strong> • free cash flow <strong>{data.free_cash_flow_concept ?? "unavailable"}</strong> • balance sheet <strong>{data.balance_sheet_concept ?? "unavailable"}</strong> • shares outstanding <strong>{data.shares_outstanding_concept ?? "unavailable"}</strong> • EPS <strong>{data.eps_concept ?? "unavailable"}</strong> • CIK {data.company.cik}. {data.cache.sec}.</div>
      </div>
      <div id="earnings-panel" role="tabpanel" aria-labelledby="earnings-tab" hidden={activeTab !== "earnings"}>
        <EarningsCallPanel key={data.company.ticker} ticker={data.company.ticker} quarters={earningsQuarters} />
      </div>
    </>}
  </main>;
}
