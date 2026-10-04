from datetime import datetime, timezone, timedelta
import time

import requests

from .config import FACTS_URL, SEC_USER_AGENT, TICKERS_URL
from . import db

HEADERS = {"User-Agent": SEC_USER_AGENT, "Accept-Encoding": "gzip, deflate"}

class SecError(RuntimeError):
    pass

def now_iso():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()

def parse_time(value):
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))

def ensure_ticker_map():
    cached = db.get_meta("ticker_map_fetched_at")
    if cached and datetime.now(timezone.utc) - parse_time(cached) < timedelta(days=7):
        return
    response = requests.get(TICKERS_URL, headers=HEADERS, timeout=30)
    response.raise_for_status()
    data = response.json()
    rows = []
    for item in data.values():
        rows.append((item["ticker"].upper(), int(item["cik_str"]), item["title"], item.get("exchange", "")))
    db.upsert_companies(rows)
    db.set_meta("ticker_map_fetched_at", now_iso())

def get_company(ticker):
    ticker = ticker.upper().strip()
    ensure_ticker_map()
    company = db.get_company(ticker)
    if not company:
        raise SecError(f"Ticker {ticker} was not found in the SEC ticker list.")
    return company

def get_company_facts(cik):
    cached = db.get_facts(cik)
    if cached:
        fetched = parse_time(cached["fetched_at"])
        if fetched and datetime.now(timezone.utc) - fetched < timedelta(hours=24):
            return cached["payload"]
    time.sleep(0.15)
    response = requests.get(FACTS_URL.format(cik=cik), headers=HEADERS, timeout=30)
    response.raise_for_status()
    payload = response.json()
    db.save_facts(cik, payload, now_iso())
    return payload
