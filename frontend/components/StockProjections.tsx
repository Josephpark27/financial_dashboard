"use client";

import { useEffect, useMemo, useState } from "react";
import type { DashboardData, Metric } from "./Dashboard";

type CaseKey = "bear" | "base" | "bull";
type CaseInputs = {
  revenueGrowth: number;
  netMargin: number;
  exitPe: number;
  shareGrowth: number;
  outcomeWeight: number;
};
type ProjectionModel = {
  horizon: 3 | 4 | 5;
  currentPrice: number;
  startingRevenue: number;
  startingNetIncome: number;
  startingShares: number;
  cases: Record<CaseKey, CaseInputs>;
};
type CaseResult = {
  revenue: number;
  netIncome: number;
  impliedMargin: number | null;
  netIncomeGrowth: number | null;
  shares: number;
  eps: number | null;
  targetPrice: number | null;
  upside: number | null;
  cagr: number | null;
};

const CASES: { key: CaseKey; title: string; subtitle: string }[] = [
  { key: "bear", title: "Bear", subtitle: "Growth stalls · margins slip · multiple stays compressed" },
  { key: "base", title: "Base", subtitle: "Growth steps down · margins stay flat · multiple below market" },
  { key: "bull", title: "Bull", subtitle: "Growth accelerates · margins expand · multiple rises" },
];

function clamp(value: number, min: number, max: number) {
  return Math.min(max, Math.max(min, value));
}

function rounded(value: number, places = 1) {
  return Number(value.toFixed(places));
}

function annualBaseline(rows: Metric[] | undefined) {
  if (!rows?.length) return { value: 0, note: "No reported data" };
  const latest = rows.at(-1)!;
  if (latest.ttm_value != null && Number.isFinite(latest.ttm_value)) {
    return { value: latest.ttm_value, note: "Latest reported TTM" };
  }
  if (rows.length >= 4) {
    return { value: rows.slice(-4).reduce((total, row) => total + row.value, 0), note: "Sum of latest four reported quarters" };
  }
  return { value: latest.value * 4, note: "Latest quarter annualized × 4" };
}

function defaultModel(data: DashboardData): ProjectionModel {
  const revenue = annualBaseline(data.revenue);
  const income = annualBaseline(data.net_income);
  const latestRevenue = data.revenue.at(-1);
  const historicalRevenueGrowth = latestRevenue?.ttm_yoy_pct ?? latestRevenue?.yoy_pct ?? 5;
  const revenueGrowth = rounded(clamp(historicalRevenueGrowth, -90, 100));
  const startingMargin = revenue.value > 0 ? income.value / revenue.value * 100 : 0;
  const netMargin = rounded(clamp(startingMargin, -100, 100));
  const market = data.market;
  const marketPe = market.forward_pe != null && market.forward_pe > 0
    ? market.forward_pe
    : market.trailing_pe != null && market.trailing_pe > 0 ? market.trailing_pe : 20;
  const latestShares = data.shares_outstanding?.at(-1);
  const price = market.market_price ?? 0;
  const marketCapShares = market.market_cap != null && price > 0 ? market.market_cap / price : 0;
  const shareCount = latestShares?.value && latestShares.value > 0 ? latestShares.value : marketCapShares;
  const shareGrowth = rounded(clamp(latestShares?.yoy_pct ?? 0, -50, 100));

  const makeCase = (caseRevenueGrowth: number, caseNetMargin: number, pe: number, outcomeWeight: number): CaseInputs => ({
    revenueGrowth: rounded(clamp(caseRevenueGrowth, -99, 200)),
    netMargin: rounded(clamp(caseNetMargin, -100, 100)),
    exitPe: rounded(clamp(pe, 0, 200), 1),
    shareGrowth: rounded(clamp(shareGrowth, -99, 200)),
    outcomeWeight,
  });

  const bearRevenueGrowth = Math.min(0, revenueGrowth - 10);
  const bullRevenueGrowth = revenueGrowth + 10;

  return {
    horizon: 3,
    currentPrice: price,
    startingRevenue: revenue.value,
    startingNetIncome: income.value,
    startingShares: shareCount,
    cases: {
      bear: makeCase(bearRevenueGrowth, netMargin - 5, marketPe, 20),
      base: makeCase(revenueGrowth, netMargin, marketPe * 0.9, 60),
      bull: makeCase(bullRevenueGrowth, netMargin + 5, marketPe * 1.25, 20),
    },
  };
}

function safeNumber(value: unknown, fallback: number, min: number, max: number) {
  const number = typeof value === "number" ? value : Number(value);
  return Number.isFinite(number) ? clamp(number, min, max) : fallback;
}

function legacyNetMargin(netIncomeGrowth: number | undefined, revenueGrowth: number, startingRevenue: number, startingNetIncome: number, horizon: number) {
  if (netIncomeGrowth == null || startingRevenue <= 0) return null;
  const projectedRevenue = startingRevenue * Math.pow(1 + revenueGrowth / 100, horizon);
  if (projectedRevenue <= 0) return null;
  const projectedNetIncome = startingNetIncome * Math.pow(1 + netIncomeGrowth / 100, horizon);
  return projectedNetIncome / projectedRevenue * 100;
}

function restoreModel(value: unknown, defaults: ProjectionModel): ProjectionModel {
  if (!value || typeof value !== "object") return defaults;
  const saved = value as Partial<ProjectionModel>;
  type SavedCaseInputs = Partial<CaseInputs> & { netIncomeGrowth?: number };
  const savedCases = saved.cases as Partial<Record<CaseKey, SavedCaseInputs>> | undefined;
  const horizon = saved.horizon === 4 || saved.horizon === 5 ? saved.horizon : 3;
  const startingRevenue = safeNumber(saved.startingRevenue, defaults.startingRevenue, 0, 1e16);
  const startingNetIncome = safeNumber(saved.startingNetIncome, defaults.startingNetIncome, -1e16, 1e16);
  const cases = {} as Record<CaseKey, CaseInputs>;
  for (const key of ["bear", "base", "bull"] as const) {
    const source = savedCases?.[key];
    const fallback = defaults.cases[key];
    const revenueGrowth = safeNumber(source?.revenueGrowth, fallback.revenueGrowth, -99, 200);
    const inferredNetMargin = legacyNetMargin(source?.netIncomeGrowth, revenueGrowth, startingRevenue, startingNetIncome, horizon);
    cases[key] = {
      revenueGrowth,
      netMargin: safeNumber(source?.netMargin, inferredNetMargin ?? fallback.netMargin, -100, 100),
      exitPe: safeNumber(source?.exitPe, fallback.exitPe, 0, 200),
      shareGrowth: safeNumber(source?.shareGrowth, fallback.shareGrowth, -99, 200),
      outcomeWeight: safeNumber(source?.outcomeWeight, fallback.outcomeWeight, 0, 100),
    };
  }
  return {
    horizon,
    currentPrice: safeNumber(saved.currentPrice, defaults.currentPrice, 0, 1_000_000),
    startingRevenue,
    startingNetIncome,
    startingShares: safeNumber(saved.startingShares, defaults.startingShares, 0, 1e15),
    cases,
  };
}

function formatMoney(value: number | null, currency: string, compact = true) {
  if (value == null || !Number.isFinite(value)) return "—";
  try {
    return new Intl.NumberFormat(undefined, {
      style: "currency",
      currency: currency || "USD",
      notation: compact ? "compact" : "standard",
      maximumFractionDigits: compact ? 2 : 2,
    }).format(value);
  } catch {
    return `${value.toLocaleString(undefined, { maximumFractionDigits: 2 })} ${currency}`;
  }
}

function formatShares(value: number | null) {
  if (value == null || !Number.isFinite(value)) return "—";
  return new Intl.NumberFormat(undefined, { notation: "compact", maximumFractionDigits: 2 }).format(value);
}

function formatPercent(value: number | null, places = 1) {
  return value == null || !Number.isFinite(value) ? "—" : `${value.toFixed(places)}%`;
}

function projectCase(model: ProjectionModel, key: CaseKey): CaseResult {
  const assumptions = model.cases[key];
  const revenue = model.startingRevenue * Math.pow(1 + assumptions.revenueGrowth / 100, model.horizon);
  const netIncome = revenue * assumptions.netMargin / 100;
  const impliedMargin = revenue > 0 ? netIncome / revenue * 100 : null;
  const netIncomeGrowth = model.startingNetIncome > 0 && netIncome > 0
    ? (Math.pow(netIncome / model.startingNetIncome, 1 / model.horizon) - 1) * 100
    : null;
  const shares = model.startingShares * Math.pow(1 + assumptions.shareGrowth / 100, model.horizon);
  const eps = shares > 0 ? netIncome / shares : null;
  const targetPrice = eps != null && eps > 0 && assumptions.exitPe > 0 ? eps * assumptions.exitPe : null;
  const upside = targetPrice != null && model.currentPrice > 0
    ? (targetPrice / model.currentPrice - 1) * 100
    : null;
  const cagr = targetPrice != null && model.currentPrice > 0
    ? (Math.pow(targetPrice / model.currentPrice, 1 / model.horizon) - 1) * 100
    : null;
  return { revenue, netIncome, impliedMargin, netIncomeGrowth, shares, eps, targetPrice, upside, cagr };
}

function probabilityWeightedPrice(model: ProjectionModel, results: Record<CaseKey, CaseResult>) {
  const active = CASES.map(({ key }) => ({ key, weight: model.cases[key].outcomeWeight, price: results[key].targetPrice }))
    .filter(item => item.weight > 0);
  if (!active.length || active.some(item => item.price == null)) return null;
  const totalWeight = active.reduce((sum, item) => sum + item.weight, 0);
  if (totalWeight <= 0) return null;
  return active.reduce((sum, item) => sum + (item.price ?? 0) * item.weight, 0) / totalWeight;
}

function scenarioReturnRange(results: Record<CaseKey, CaseResult>) {
  const returns = Object.values(results).map(result => result.cagr).filter((value): value is number => value != null && Number.isFinite(value));
  if (!returns.length) return { low: null, high: null };
  return { low: Math.min(...returns), high: Math.max(...returns) };
}

function NumberField({ label, value, suffix, min, max, step, onChange }: {
  label: string;
  value: number;
  suffix?: string;
  min: number;
  max: number;
  step: number;
  onChange: (value: number) => void;
}) {
  const [draft, setDraft] = useState(String(value));

  useEffect(() => {
    setDraft(String(value));
  }, [value]);

  return <label className="projection-field">
    <span>{label}</span>
    <span className="projection-number-wrap">
      <input type="number" value={draft} min={min} max={max} step={step}
        onChange={event => {
          const next = event.target.value;
          setDraft(next);
          if (next.trim() === "" || next === "-" || next.endsWith(".")) return;
          const number = Number(next);
          if (Number.isFinite(number)) onChange(number);
        }}
        onBlur={() => {
          if (draft.trim() === "" || !Number.isFinite(Number(draft))) setDraft(String(value));
        }} />
      {suffix && <small>{suffix}</small>}
    </span>
  </label>;
}

export default function StockProjections({ data }: { data: DashboardData }) {
  const defaults = useMemo(() => defaultModel(data), [data]);
  const [model, setModel] = useState<ProjectionModel>(defaults);
  const [ready, setReady] = useState(false);
  const currency = data.accounting_currency ?? "USD";
  const storageKey = `financial-dashboard:stock-projections:${data.company.ticker}`;

  useEffect(() => {
    setReady(false);
    try {
      const saved = window.localStorage.getItem(storageKey);
      setModel(saved ? restoreModel(JSON.parse(saved), defaults) : defaults);
    } catch {
      setModel(defaults);
    }
    setReady(true);
  }, [storageKey, defaults]);

  useEffect(() => {
    if (!ready) return;
    try {
      window.localStorage.setItem(storageKey, JSON.stringify(model));
    } catch {
      // The projection still works if browser storage is unavailable.
    }
  }, [model, ready, storageKey]);

  const results = useMemo(() => ({
    bear: projectCase(model, "bear"),
    base: projectCase(model, "base"),
    bull: projectCase(model, "bull"),
  }), [model]);
  const weightedPrice = probabilityWeightedPrice(model, results);
  const weightedCagr = weightedPrice != null && model.currentPrice > 0
    ? (Math.pow(weightedPrice / model.currentPrice, 1 / model.horizon) - 1) * 100
    : null;
  const outcomeWeightTotal = CASES.reduce((total, { key }) => total + model.cases[key].outcomeWeight, 0);
  const returnRange = scenarioReturnRange(results);
  const netIncomeGrowthUnavailable = model.startingNetIncome <= 0 || Object.values(results).some(result => result.netIncome <= 0);
  const currentYear = new Date().getFullYear();
  const targetYear = currentYear + model.horizon;

  function updateCase(key: CaseKey, field: keyof CaseInputs, value: number) {
    if (!Number.isFinite(value)) return;
    setModel(current => ({
      ...current,
      cases: { ...current.cases, [key]: { ...current.cases[key], [field]: value } },
    }));
  }

  function reset() {
    setModel(defaultModel(data));
  }

  const revenueBaseline = annualBaseline(data.revenue);
  const incomeBaseline = annualBaseline(data.net_income);
  const currentMargin = model.startingRevenue > 0 ? model.startingNetIncome / model.startingRevenue * 100 : null;
  const latestRevenueGrowth = data.revenue.at(-1)?.ttm_yoy_pct ?? data.revenue.at(-1)?.yoy_pct ?? null;
  const latestNetIncomeGrowth = data.net_income.at(-1)?.ttm_yoy_pct ?? data.net_income.at(-1)?.yoy_pct ?? null;
  const latestShareCountGrowth = data.shares_outstanding?.at(-1)?.yoy_pct ?? null;
  const historicalMargins = (data.margins ?? []).map(row => row.net_margin_ttm ?? row.net_margin)
    .filter((margin): margin is number => margin != null && Number.isFinite(margin));
  const historicalMarginHigh = historicalMargins.length ? Math.max(...historicalMargins) : null;
  const shareSource = data.shares_outstanding?.at(-1)?.value
    ? "Latest SEC shares outstanding"
    : data.market.market_cap != null && model.currentPrice > 0
      ? "Estimated from market cap ÷ current price"
      : "Enter an estimate to calculate EPS";

  return <section className="stock-projections-panel" aria-labelledby="projection-title">
    <header className="projection-header">
      <div>
        <h2 id="projection-title">Stock projections</h2>
        <p>Project revenue and target-year net margin, then use implied net income growth as a check and an exit P/E to estimate value.</p>
      </div>
      <button type="button" className="projection-reset" onClick={reset}>Reset assumptions</button>
    </header>

    <div className="projection-baseline-grid">
      <div className="projection-baseline-card">
        <span>Latest annual revenue</span>
        <strong>{formatMoney(model.startingRevenue, currency)}</strong>
        <small>{revenueBaseline.note}</small>
        <small>Latest YoY: {formatPercent(latestRevenueGrowth)}</small>
      </div>
      <div className="projection-baseline-card">
        <span>Latest net margin</span>
        <strong>{formatPercent(currentMargin)}</strong>
        <small>Net income ÷ revenue</small>
      </div>
      <div className="projection-baseline-card">
        <span>Latest annual net income</span>
        <strong>{formatMoney(model.startingNetIncome, currency)}</strong>
        <small>{incomeBaseline.note} · editable below</small>
        <small>Latest YoY: {formatPercent(latestNetIncomeGrowth)}</small>
      </div>
      <div className="projection-baseline-card">
        <span>Latest share count change</span>
        <strong>{formatPercent(latestShareCountGrowth)} / yr</strong>
        <small>Latest SEC year-over-year change</small>
      </div>
    </div>

    <section className="projection-common-inputs" aria-label="Starting assumptions">
      <div className="projection-section-heading">
        <div><h3>Starting assumptions</h3><p>SEC values prefill the model. Edit them to use normalized earnings or a different entry price.</p></div>
        <div className="projection-horizon">
          <span>Forecast horizon</span>
          <div className="segmented-control" role="group" aria-label="Forecast horizon">
            {([3, 4, 5] as const).map(year => <button type="button" key={year}
              aria-pressed={model.horizon === year} onClick={() => setModel(current => ({ ...current, horizon: year }))}>
              {year} years · {currentYear + year}
            </button>)}
          </div>
        </div>
      </div>
      <div className="projection-common-grid">
        <NumberField label="Starting annual revenue" value={model.startingRevenue} suffix={currency}
          min={0} max={1e16} step={1_000_000} onChange={value => setModel(current => ({ ...current, startingRevenue: clamp(value, 0, 1e16) }))} />
        <NumberField label="Starting annual net income" value={model.startingNetIncome} suffix={currency}
          min={-1e16} max={1e16} step={1_000_000} onChange={value => setModel(current => ({ ...current, startingNetIncome: clamp(value, -1e16, 1e16) }))} />
        <NumberField label="Starting shares outstanding" value={model.startingShares} suffix="shares"
          min={0} max={1e15} step={1_000_000} onChange={value => setModel(current => ({ ...current, startingShares: clamp(value, 0, 1e15) }))} />
        <NumberField label="Share price today" value={model.currentPrice} suffix="USD"
          min={0} max={1_000_000} step={0.01} onChange={value => setModel(current => ({ ...current, currentPrice: clamp(value, 0, 1_000_000) }))} />
      </div>
      <p className="projection-data-note">Share count: {shareSource}. Starting values use reported TTM where available; otherwise they sum the latest four quarters or annualize the latest quarter.</p>
    </section>

    <section className="projection-scenario-section" aria-label="Scenario assumptions">
      <div className="projection-section-heading">
        <div><h3>Scenario assumptions</h3><p>Set revenue growth, target-year net margin, exit P/E, and share dilution. Net income growth is derived from revenue and margin.</p></div>
        <span className="projection-save-status" role="status">{ready ? "Saved by ticker in this browser" : "Loading saved assumptions…"}</span>
      </div>
      <p className="projection-data-note">Revenue growth and Base margin default is from recent SEC trends; Bear and Bull vary them. Exit P/E default is from the current quote multiple; compare it with industry peers. Share count change default is from the latest year-over-year SEC change.</p>
      {netIncomeGrowthUnavailable && <p className="projection-data-note">Derived net income growth is unavailable for scenarios with non-positive starting or projected net income. Use normalized starting earnings where appropriate.</p>}
      <div className="projection-case-grid">
        {CASES.map(({ key, title, subtitle }) => {
          const result = results[key];
          const marginAboveHistory = result.impliedMargin != null && historicalMarginHigh != null
            && result.impliedMargin > historicalMarginHigh;
          return <article className={`projection-case-card ${key}`} key={key}>
          <header><div><h4>{title}</h4><p>{subtitle}</p></div><span className="projection-case-dot" aria-hidden="true" /></header>
          <NumberField label="Revenue growth" value={model.cases[key].revenueGrowth} suffix="% / yr"
            min={-99} max={200} step={1} onChange={value => updateCase(key, "revenueGrowth", clamp(value, -99, 200))} />
          <NumberField label={`Net margin in ${targetYear}`} value={model.cases[key].netMargin} suffix="%"
            min={-100} max={100} step={1} onChange={value => updateCase(key, "netMargin", clamp(value, -100, 100))} />
          <div className={`projection-margin-backstop${marginAboveHistory ? " above-history" : ""}`}>
            <span>Derived net income growth</span>
            <strong>{formatPercent(result.netIncomeGrowth)}</strong>
            <small>Historical margin high: {formatPercent(historicalMarginHigh)}</small>
            {marginAboveHistory && <em>Note: Target margin is above reported history</em>}
          </div>
          <NumberField label="Exit P/E" value={model.cases[key].exitPe} suffix="×"
            min={0} max={200} step={1} onChange={value => updateCase(key, "exitPe", clamp(value, 0, 200))} />
          <NumberField label="Share count change" value={model.cases[key].shareGrowth} suffix="% / yr"
            min={-99} max={200} step={0.5} onChange={value => updateCase(key, "shareGrowth", clamp(value, -99, 200))} />
          <NumberField label="Outcome weight" value={model.cases[key].outcomeWeight} suffix="%"
            min={0} max={100} step={5} onChange={value => updateCase(key, "outcomeWeight", clamp(value, 0, 100))} />
        </article>;
        })}
      </div>
    </section>

    <section className="projection-results" aria-labelledby="projection-results-title">
      <div className="projection-section-heading">
        <div><h3 id="projection-results-title">Year {targetYear} outcomes</h3><p>Fair value is projected EPS multiplied by your exit P/E assumption.</p></div>
      </div>
      <div className="projection-outcome-grid">
        {CASES.map(({ key, title }) => {
          const result = results[key];
          return <article key={key} className={`projection-outcome-card ${key}`}>
            <span className="projection-outcome-label">{title} case · {formatPercent(model.cases[key].outcomeWeight, 0)} weight</span>
            <strong className="projection-target-price">{formatMoney(result.targetPrice, "USD", false)}</strong>
            <div className="projection-return-line">
              <span>{formatPercent(result.cagr)} annualized</span>
              <span>{formatPercent(result.upside)} total</span>
            </div>
            <dl>
              <div><dt>Revenue</dt><dd>{formatMoney(result.revenue, currency)}</dd></div>
              <div><dt>Net income</dt><dd>{formatMoney(result.netIncome, currency)}</dd></div>
              <div><dt>EPS</dt><dd>{formatMoney(result.eps, "USD", false)}</dd></div>
              <div><dt>Share count</dt><dd>{formatShares(result.shares)}</dd></div>
            </dl>
            {result.targetPrice == null && <p className="projection-result-note">Positive EPS and a share count are needed for P/E valuation.</p>}
          </article>;
        })}
      </div>
      <div className="projection-benchmark">
        <div>
          <span>Scenario return range</span>
          <strong>{formatPercent(returnRange.low)} to {formatPercent(returnRange.high)} annualized</strong>
          <small>Lowest to highest modeled case</small>
        </div>
        <div>
          <span>Probability-weighted price</span>
          <strong>{formatMoney(weightedPrice, "USD", false)}</strong>
          <small>{formatPercent(weightedCagr)} annualized · {formatPercent(outcomeWeightTotal, 0)} total weight</small>
        </div>
      </div>
      {Math.abs(outcomeWeightTotal - 100) > 0.01 && <p className="projection-data-note">Outcome weights currently total {formatPercent(outcomeWeightTotal, 0)}; the weighted price normalizes them to 100%.</p>}
      <div className="projection-table-wrap">
        <table className="projection-table">
          <thead><tr><th>Year {targetYear} metric</th><th>Bear</th><th>Base</th><th>Bull</th></tr></thead>
          <tbody>
            <tr><th scope="row">Revenue</th><td>{formatMoney(results.bear.revenue, currency)}</td><td>{formatMoney(results.base.revenue, currency)}</td><td>{formatMoney(results.bull.revenue, currency)}</td></tr>
            <tr><th scope="row">Revenue growth</th><td>{formatPercent(model.cases.bear.revenueGrowth)}</td><td>{formatPercent(model.cases.base.revenueGrowth)}</td><td>{formatPercent(model.cases.bull.revenueGrowth)}</td></tr>
            <tr><th scope="row">Net margin</th><td>{formatPercent(model.cases.bear.netMargin)}</td><td>{formatPercent(model.cases.base.netMargin)}</td><td>{formatPercent(model.cases.bull.netMargin)}</td></tr>
            <tr><th scope="row">Derived net income growth</th><td>{formatPercent(results.bear.netIncomeGrowth)}</td><td>{formatPercent(results.base.netIncomeGrowth)}</td><td>{formatPercent(results.bull.netIncomeGrowth)}</td></tr>
            <tr><th scope="row">Net income</th><td>{formatMoney(results.bear.netIncome, currency)}</td><td>{formatMoney(results.base.netIncome, currency)}</td><td>{formatMoney(results.bull.netIncome, currency)}</td></tr>
            <tr><th scope="row">EPS</th><td>{formatMoney(results.bear.eps, "USD", false)}</td><td>{formatMoney(results.base.eps, "USD", false)}</td><td>{formatMoney(results.bull.eps, "USD", false)}</td></tr>
            <tr><th scope="row">Exit P/E</th><td>{model.cases.bear.exitPe.toFixed(1)}×</td><td>{model.cases.base.exitPe.toFixed(1)}×</td><td>{model.cases.bull.exitPe.toFixed(1)}×</td></tr>
            <tr><th scope="row">Outcome weight</th><td>{formatPercent(model.cases.bear.outcomeWeight, 0)}</td><td>{formatPercent(model.cases.base.outcomeWeight, 0)}</td><td>{formatPercent(model.cases.bull.outcomeWeight, 0)}</td></tr>
            <tr><th scope="row">Implied price</th><td>{formatMoney(results.bear.targetPrice, "USD", false)}</td><td>{formatMoney(results.base.targetPrice, "USD", false)}</td><td>{formatMoney(results.bull.targetPrice, "USD", false)}</td></tr>
            <tr><th scope="row">Annualized return</th><td>{formatPercent(results.bear.cagr)}</td><td>{formatPercent(results.base.cagr)}</td><td>{formatPercent(results.bull.cagr)}</td></tr>
          </tbody>
        </table>
      </div>
    </section>
    <p className="projection-disclaimer">Scenario math is a planning aid, not a prediction or investment recommendation. A P/E valuation is not meaningful when projected EPS is zero or negative; consider a different valuation method for those companies.</p>
  </section>;
}
