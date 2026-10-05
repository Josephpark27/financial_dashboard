import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / "backend" / ".env")

DB_PATH = Path(os.getenv("DB_PATH", str(ROOT / "data" / "financial_dashboard.sqlite3")))
if not DB_PATH.is_absolute():
    DB_PATH = (ROOT / "backend" / DB_PATH).resolve()
DB_PATH.parent.mkdir(parents=True, exist_ok=True)

SEC_USER_AGENT = os.getenv(
    "SEC_USER_AGENT",
    "Personal SEC Financial Dashboard your-email@example.com",
)
MARKET_DATA_ENABLED = os.getenv("MARKET_DATA_ENABLED", "true").lower() in {"1", "true", "yes"}
ALPHAVANTAGE_API_KEY = os.getenv("ALPHAVANTAGE_API_KEY", "").strip()

TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"
