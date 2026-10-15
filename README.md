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
│   ├── Yahoo Finance quote and Forward P/E data
│   ├── On-demand Alpha Vantage earnings call transcripts
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
- Forward P/E uses Yahoo Finance forward EPS or its reported forward P/E. Alpha Vantage is used only for earnings call transcripts.
- Earnings call transcripts are requested only after the user selects a fiscal quarter and presses **Fetch transcript**; they are not fetched during dashboard loading.
- The Earnings Call tab checks SQLite for the selected quarter's transcript on load and displays it automatically when cached. This cache-only check does not contact Alpha Vantage or Google AI Studio.
- Successful transcripts are cached in local SQLite by ticker and fiscal quarter until the local cache is cleared. Google AI Studio summaries are keyed by ticker, quarter, and model, and expire after 24 hours; an expired summary is regenerated on the next fetch.
- Transcript summaries are generated only as part of an explicit **Fetch transcript** request. Set `GEMINI_API_KEY` in `backend/.env` to enable summaries; `GEMINI_MODEL` defaults to `gemini-3.5-flash-lite`. Google AI Studio's free-tier access and limits depend on the selected model and account/project.
- All raw SEC Company Facts are stored in SQLite so the dashboard can be rebuilt without another SEC request while the cache is fresh.
- `data/financial_dashboard.sqlite3` is intentionally ignored by git.

## Data model

- `app_meta`: cache metadata.
- `companies`: SEC ticker/CIK/company metadata.
- `sec_company_facts`: raw Company Facts JSON by CIK.
- `quarterly_metrics`: normalized quarterly revenue/net income facts.
- `market_snapshots`: latest price/forward P/E snapshot.

To enable earnings call transcripts, claim an Alpha Vantage API key from [Alpha Vantage](https://www.alphavantage.co/support/#api-key) and add it as `ALPHAVANTAGE_API_KEY` in `backend/.env`. To enable summaries, create a Google AI Studio API key and add it as `GEMINI_API_KEY` (optionally set `GEMINI_MODEL`) in the same file. The Earnings Call tab sends provider requests for the selected ticker and fiscal quarter only when **Fetch transcript** is pressed. If summary generation fails, the transcript is still displayed.

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
