from datetime import datetime, timezone, timedelta

from . import db
from .config import MARKET_DATA_ENABLED


def now_iso():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def snapshot(ticker):
    if not MARKET_DATA_ENABLED:
        return {"market_price": None, "forward_pe": None, "fetched_at": None}
    cached = db.get_market_snapshot(ticker)
    if cached:
        fetched = datetime.fromisoformat(cached["fetched_at"].replace("Z", "+00:00"))
        if datetime.now(timezone.utc) - fetched < timedelta(minutes=15):
            return cached
    try:
        import yfinance as yf
        info = yf.Ticker(ticker).info or {}
        price = info.get("regularMarketPrice") or info.get("previousClose")
        forward_pe = info.get("forwardPE")
        fetched_at = now_iso()
        db.save_market_snapshot(ticker, price, forward_pe, fetched_at)
        return {"market_price": price, "forward_pe": forward_pe, "fetched_at": fetched_at}
    except Exception:
        return cached or {"market_price": None, "forward_pe": None, "fetched_at": None}
