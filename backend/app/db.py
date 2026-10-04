import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from .config import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS app_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS companies (
    ticker TEXT PRIMARY KEY,
    cik INTEGER NOT NULL,
    name TEXT NOT NULL,
    exchange TEXT,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_companies_cik ON companies(cik);
CREATE TABLE IF NOT EXISTS sec_company_facts (
    cik INTEGER PRIMARY KEY,
    payload TEXT NOT NULL,
    fetched_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS quarterly_metrics (
    ticker TEXT NOT NULL,
    metric TEXT NOT NULL,
    period_end TEXT NOT NULL,
    start_date TEXT,
    fiscal_period TEXT,
    filed TEXT,
    value REAL NOT NULL,
    yoy_pct REAL,
    source TEXT,
    concept TEXT,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (ticker, metric, period_end)
);
CREATE INDEX IF NOT EXISTS idx_quarterly_metrics_ticker_metric
ON quarterly_metrics(ticker, metric, period_end);
CREATE TABLE IF NOT EXISTS market_snapshots (
    ticker TEXT PRIMARY KEY,
    market_price REAL,
    forward_pe REAL,
    fetched_at TEXT NOT NULL
);
"""

@contextmanager
def connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()

def init_db():
    with connection() as conn:
        conn.executescript(SCHEMA)

def get_meta(key):
    with connection() as conn:
        row = conn.execute("SELECT value FROM app_meta WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else None

def set_meta(key, value):
    with connection() as conn:
        conn.execute(
            "INSERT INTO app_meta(key, value, updated_at) VALUES (?, ?, CURRENT_TIMESTAMP) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=CURRENT_TIMESTAMP",
            (key, value),
        )

def upsert_companies(companies):
    with connection() as conn:
        conn.executemany(
            "INSERT INTO companies(ticker,cik,name,exchange,updated_at) VALUES (?,?,?,?,CURRENT_TIMESTAMP) "
            "ON CONFLICT(ticker) DO UPDATE SET cik=excluded.cik,name=excluded.name,exchange=excluded.exchange,updated_at=CURRENT_TIMESTAMP",
            companies,
        )

def get_company(ticker):
    with connection() as conn:
        row = conn.execute("SELECT * FROM companies WHERE ticker = ?", (ticker,)).fetchone()
        return dict(row) if row else None

def save_facts(cik, payload, fetched_at):
    with connection() as conn:
        conn.execute(
            "INSERT INTO sec_company_facts(cik,payload,fetched_at) VALUES (?,?,?) "
            "ON CONFLICT(cik) DO UPDATE SET payload=excluded.payload,fetched_at=excluded.fetched_at",
            (cik, json.dumps(payload), fetched_at),
        )

def get_facts(cik):
    with connection() as conn:
        row = conn.execute("SELECT * FROM sec_company_facts WHERE cik = ?", (cik,)).fetchone()
        if not row:
            return None
        return {"payload": json.loads(row["payload"]), "fetched_at": row["fetched_at"]}

def replace_metrics(ticker, metric, rows):
    with connection() as conn:
        conn.execute("DELETE FROM quarterly_metrics WHERE ticker = ? AND metric = ?", (ticker, metric))
        conn.executemany(
            """INSERT INTO quarterly_metrics
            (ticker,metric,period_end,start_date,fiscal_period,filed,value,yoy_pct,source,concept,updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP)""",
            [
                (ticker, metric, r["period_end"], r.get("start_date"), r.get("fiscal_period"),
                 r.get("filed"), r["value"], r.get("yoy_pct"), r.get("source"), r.get("concept"))
                for r in rows
            ],
        )

def get_metrics(ticker, metric):
    with connection() as conn:
        rows = conn.execute(
            "SELECT * FROM quarterly_metrics WHERE ticker = ? AND metric = ? ORDER BY period_end",
            (ticker, metric),
        ).fetchall()
        return [dict(r) for r in rows]

def save_market_snapshot(ticker, price, forward_pe, fetched_at):
    with connection() as conn:
        conn.execute(
            "INSERT INTO market_snapshots(ticker,market_price,forward_pe,fetched_at) VALUES (?,?,?,?) "
            "ON CONFLICT(ticker) DO UPDATE SET market_price=excluded.market_price,forward_pe=excluded.forward_pe,fetched_at=excluded.fetched_at",
            (ticker, price, forward_pe, fetched_at),
        )

def get_market_snapshot(ticker):
    with connection() as conn:
        row = conn.execute("SELECT * FROM market_snapshots WHERE ticker = ?", (ticker,)).fetchone()
        return dict(row) if row else None
