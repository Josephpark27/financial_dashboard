from contextlib import asynccontextmanager
from datetime import date, timedelta

from fastapi import Body, FastAPI, HTTPException, Query

from . import db
from .metrics import add_growth_metrics, balance_sheet_metric, free_cash_flow_metric, liquidity_debt_metric, margin_metric, quarter_metric, shares_outstanding_metric
from .market import earnings_call_transcript, earnings_event, historical_pe, snapshot
from .sec import SecError, get_company, get_company_facts, open_sec_filing_pdf, quarterly_filing_links
from .fallback import fallback_needs_refresh, refresh_six_k_fallback, six_k_fallback


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    yield


app = FastAPI(title="Local SEC Financial Dashboard API", lifespan=lifespan)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/api/filing-pdf")
def open_filing_pdf(url: str = Query(..., min_length=1, max_length=2048)):
    try:
        open_sec_filing_pdf(url)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {"status": "opened"}


@app.post("/api/earnings-transcript")
def fetch_earnings_transcript(
    ticker: str = Body(..., embed=True),
    quarter: str = Body(..., embed=True),
):
    try:
        return earnings_call_transcript(ticker, quarter)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


def build_metric(facts, cik, years, kind):
    try:
        return quarter_metric(facts, years, kind)
    except ValueError:
        if fallback_needs_refresh(cik):
            refresh_six_k_fallback(cik, years)
        concept, rows = six_k_fallback(cik, kind)
        quarters = []
        for row in sorted(rows, key=lambda r: r["end"]):
            quarters.append({
                "start": row["start"], "end": row["end"], "value": float(row["val"]),
                "filed": row.get("filed"), "fy": row.get("fy"), "fp": row.get("fp"),
                "days": 92, "source": "6-K earnings release", "concept": concept,
            })
        add_growth_metrics(quarters)
        for row in quarters:
            fy, fp = row.get("fy"), row.get("fp")
            row["fiscal_period"] = f"{int(fy)} {fp}" if fy is not None and fp else "-"
            row["period_end"] = row["end"]
            row["start_date"] = row["start"]
            row["concept"] = concept
        cutoff = __import__("datetime").date.today() - __import__("datetime").timedelta(days=365 * years)
        quarters = [q for q in quarters if __import__("datetime").date.fromisoformat(q["period_end"]) >= cutoff]
        if not quarters:
            raise ValueError(f"Could not identify quarterly {kind} facts in the SEC data.")
        return concept, quarters


@app.get("/api/dashboard")
def dashboard(ticker: str = Query("AAPL", min_length=1, max_length=10), years: int = Query(3, ge=2, le=5)):
    ticker = ticker.strip().upper()
    try:
        company = get_company(ticker)
        facts = get_company_facts(company["cik"])
        revenue_concept, revenue = build_metric(facts, company["cik"], years, "revenue")
        income_concept, net_income = build_metric(facts, company["cik"], years, "net income")
        _, margin_revenue = build_metric(facts, company["cik"], years + 1, "revenue")
        _, margin_income = build_metric(facts, company["cik"], years + 1, "net income")
        try:
            _, margin_gross_profit = quarter_metric(facts, years + 1, "gross profit")
        except ValueError:
            margin_gross_profit = []
        margins = margin_metric(margin_revenue, margin_income, margin_gross_profit)
        margin_cutoff = date.today() - timedelta(days=365 * years)
        margins = [row for row in margins if date.fromisoformat(row["period_end"]) >= margin_cutoff]
        try:
            eps_concept, eps = quarter_metric(facts, years, "eps")
        except ValueError:
            eps_concept, eps = "EPS not reported in SEC facts", []
        try:
            free_cash_flow_concept, free_cash_flow = free_cash_flow_metric(facts, years)
        except ValueError:
            free_cash_flow_concept, free_cash_flow = "Free cash flow unavailable in SEC facts", []
        try:
            balance_sheet_concept, balance_sheet = balance_sheet_metric(facts, years)
        except ValueError:
            balance_sheet_concept, balance_sheet = "Balance sheet unavailable in SEC facts", []
        try:
            liquidity_debt_concept, liquidity_debt = liquidity_debt_metric(facts, years)
        except ValueError:
            liquidity_debt_concept, liquidity_debt = "Cash, marketable securities, and debt unavailable in SEC facts", []
        try:
            shares_outstanding_concept, shares_outstanding = shares_outstanding_metric(facts, years)
        except ValueError:
            shares_outstanding_concept, shares_outstanding = "Shares outstanding unavailable in SEC facts", []
        pe_history = historical_pe(ticker, eps)
        market = snapshot(ticker)
        market["earnings_date"], market["earnings_date_type"] = earnings_event(ticker)
        periods_by_end = {}
        for row in [*revenue, *net_income]:
            periods_by_end.setdefault(row["period_end"], {
                "period_end": row["period_end"],
                "fiscal_period": row.get("fiscal_period"),
                "filed": row.get("filed"),
            })
        quarterly_filings = quarterly_filing_links(company["cik"], list(periods_by_end.values()))
    except SecError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Could not build dashboard: {exc}") from exc

    return {
        "company": company,
        "market": market,
        "revenue_concept": revenue_concept,
        "income_concept": income_concept,
        "eps_concept": eps_concept,
        "free_cash_flow_concept": free_cash_flow_concept,
        "balance_sheet_concept": balance_sheet_concept,
        "liquidity_debt_concept": liquidity_debt_concept,
        "shares_outstanding_concept": shares_outstanding_concept,
        "revenue": revenue,
        "net_income": net_income,
        "margins": margins,
        "eps": eps,
        "free_cash_flow": free_cash_flow,
        "balance_sheet": balance_sheet,
        "liquidity_debt": liquidity_debt,
        "shares_outstanding": shares_outstanding,
        "pe_history": pe_history,
        "quarterly_filings": quarterly_filings,
        "cache": {"sec": "SQLite / 24h", "ticker_map": "SQLite / 7d", "market": "SQLite / 15m"},
    }
