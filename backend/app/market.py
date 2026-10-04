from datetime import datetime, timezone, timedelta
import math

from . import db
from .config import MARKET_DATA_ENABLED


def now_iso():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _number(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _snapshot(price, forward_pe, fetched_at, forward_pe_status):
    return {
        "market_price": price,
        "forward_pe": forward_pe,
        "forward_pe_status": forward_pe_status,
        "fetched_at": fetched_at,
    }


def snapshot(ticker):
    if not MARKET_DATA_ENABLED:
        return _snapshot(None, None, None, "unavailable")
    cached = db.get_market_snapshot(ticker)
    if cached:
        fetched = datetime.fromisoformat(cached["fetched_at"].replace("Z", "+00:00"))
        cached_pe = _number(cached.get("forward_pe"))
        cached_status = cached.get("forward_pe_status")
        # Older cache rows have no status. Treat non-positive ratios as invalid
        # and refresh them immediately instead of returning them as P/E values.
        if cached_status is None:
            cached_status = "available" if cached_pe is not None and cached_pe > 0 else (
                "not_meaningful" if cached_pe is not None else "unavailable"
            )
        if (
            (cached_pe is None or cached_pe > 0)
            and datetime.now(timezone.utc) - fetched < timedelta(minutes=15)
        ):
            return _snapshot(cached.get("market_price"), cached_pe, cached["fetched_at"], cached_status)
    try:
        import yfinance as yf
        info = yf.Ticker(ticker).info or {}
        price = next((value for value in (
            _number(info.get("regularMarketPrice")),
            _number(info.get("currentPrice")),
            _number(info.get("previousClose")),
        ) if value is not None and value > 0), None)
        reported_pe = _number(info.get("forwardPE"))
        forward_eps = _number(info.get("forwardEps"))
        if reported_pe is not None and reported_pe > 0:
            forward_pe = reported_pe
            status = "available"
        elif price is not None and forward_eps is not None and forward_eps > 0:
            # A positive consensus EPS allows a coherent P/E even when the
            # provider omits or misreports its precomputed ratio.
            forward_pe = price / forward_eps
            status = "available"
        elif (reported_pe is not None and reported_pe <= 0) or (forward_eps is not None and forward_eps <= 0):
            # Negative-earnings P/E multiples are not meaningful comparisons.
            forward_pe = None
            status = "not_meaningful"
        else:
            forward_pe = None
            status = "unavailable"
        fetched_at = now_iso()
        db.save_market_snapshot(ticker, price, forward_pe, fetched_at, status)
        return _snapshot(price, forward_pe, fetched_at, status)
    except Exception:
        if cached:
            cached_pe = _number(cached.get("forward_pe"))
            if cached_pe is not None and cached_pe <= 0:
                # Scrub old negative ratios even when the live quote is down.
                fetched_at = now_iso()
                db.save_market_snapshot(ticker, cached.get("market_price"), None, fetched_at, "not_meaningful")
                return _snapshot(cached.get("market_price"), None, fetched_at, "not_meaningful")
            status = cached.get("forward_pe_status") or (
                "available" if cached_pe is not None else "unavailable"
            )
            return _snapshot(cached.get("market_price"), cached_pe, cached.get("fetched_at"), status)
        return _snapshot(None, None, None, "unavailable")
