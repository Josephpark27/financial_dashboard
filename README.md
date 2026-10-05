# SEC Financial Dashboard — Local Edition

A local-first conversion of the original Streamlit dashboard.

## Architecture

```text
Your computer
├── Next.js (localhost:3000)
│   └── Financial dashboard UI
├── Python / FastAPI (localhost:8000)
│   ├── SEC ticker + Company Facts ingestion
│   ├── XBRL quarterly normalization / YoY calculations
│   ├── Yahoo Finance quote and optional Alpha Vantage analyst EPS estimates
│   └── SQLite persistence
└── data/
    └── financial_dashboard.sqlite3
```

The browser talks only to Next.js. Next.js proxies requests to the local Python service. Python is the only component that reaches SEC EDGAR and the optional market-data provider.

## Why this is better for a one-person dashboard

- No login/auth system to maintain.
- No hosted database.
- SEC responses are persisted locally instead of being re-downloaded on every page load.
- SQLite gives you a durable local cache and makes future features much easier.
- Python owns the financial-data logic; Next.js owns presentation.
- You can run everything on one machine and keep it bound to `127.0.0.1`.

## Requirements

- Python 3.11+
- Node.js 20+
- npm 10+

## Setup

### 1. Configure SEC identification

Copy `.env.example` to `backend/.env` and replace the placeholder SEC contact email. The SEC asks automated requests to identify the application with a meaningful User-Agent.

### 2. Start Python

Windows PowerShell:

```powershell
cd backend
python -m venv .venv
.venv\\Scripts\\Activate.ps1
pip install -r requirements.txt
uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

macOS/Linux:

```bash
cd backend
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

### 3. Start Next.js

In another terminal:

```bash
npm install
npm run dev
```

Open http://localhost:3000.

## Production-ish local run

Build the frontend:

```bash
cd frontend
npm run build
npm run start
```

Run Python without `--reload`:

```bash
cd backend
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

## Cache behavior

- SEC ticker list: refreshed after 7 days.
- Company Facts: refreshed after 24 hours.
- Market snapshot: refreshed after 15 minutes.
- When `ALPHAVANTAGE_API_KEY` is configured, forward P/E uses Alpha Vantage quarterly consensus EPS for the next four fiscal quarters and calculates price / EPS. The estimate is cached for 24 hours to stay within the provider's free request allowance. Without a key or if Alpha Vantage has no estimate data for a ticker, the app uses Yahoo Finance estimates. Forward P/E is shown as N/M when the estimate is non-positive.
- All raw SEC Company Facts are stored in SQLite so the dashboard can be rebuilt without another SEC request while the cache is fresh.
- `data/financial_dashboard.sqlite3` is intentionally ignored by git.

## Data model

- `app_meta`: cache metadata.
- `companies`: SEC ticker/CIK/company metadata.
- `sec_company_facts`: raw Company Facts JSON by CIK.
- `quarterly_metrics`: normalized quarterly revenue/net income facts.
- `market_snapshots`: latest price/forward P/E snapshot.

To enable analyst consensus estimates, claim an Alpha Vantage API key from [Alpha Vantage](https://www.alphavantage.co/support/#api-key) and add it as `ALPHAVANTAGE_API_KEY` in `backend/.env`. Alpha Vantage lists the earnings-estimates endpoint among its fundamental data APIs, and its free service allows 25 requests per day across most datasets. If the endpoint is unavailable or has no estimates for a ticker, the dashboard falls back to Yahoo Finance and labels the source accordingly.

## Notes

This project is intentionally localhost-only. Do not expose the Python service or Next.js app directly to the public internet without adding authentication and reviewing SEC/market-data licensing requirements.


## Node.js setup

Run npm commands from the project root (`financial_dashboard_converted`). The root `package.json` uses npm workspaces to install and run the Next.js app in `frontend/`.

```powershell
npm install
npm run build
npm run dev
```

You can also run commands directly inside `frontend/` if preferred.
