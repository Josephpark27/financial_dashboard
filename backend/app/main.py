from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Query

from . import db
from .metrics import quarter_metric
from .market import snapshot
from .sec import SecError, get_company, get_company_facts

@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    yield

app = FastAPI(title="Local SEC Financial Dashboard API", lifespan=lifespan)

@app.get("/health")
def health():
    return {"status": "ok"}

@app.get("/api/dashboard")
def dashboard(ticker: str = Query("AAPL", min_length=1, max_length=10), years: int = Query(3, ge=2, le=5)):
    ticker = ticker.strip().upper()
    try:
        company = get_company(ticker)
        facts = get_company_facts(company["cik"])
        revenue_concept, revenue = quarter_metric(facts, years, "revenue")
        income_concept, net_income = quarter_metric(facts, years, "net income")
        market = snapshot(ticker)
    except SecError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Could not build dashboard: {exc}") from exc

    return {
        "company": company,
        "market": market,
        "revenue_concept": revenue_concept,
        "income_concept": income_concept,
        "revenue": revenue,
        "net_income": net_income,
        "cache": {"sec": "SQLite / 24h", "ticker_map": "SQLite / 7d", "market": "SQLite / 15m"},
    }
