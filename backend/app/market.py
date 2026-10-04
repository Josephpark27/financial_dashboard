from datetime import datetime, timezone, timedelta
import math

import requests

from . import db
from .config import FMP_API_KEY, MARKET_DATA_ENABLED

FMP_ESTIMATES_URL = "https://financialmodelingprep.com/stable/analyst-estimates"


def now_iso():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _number(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _snapshot(
    price, forward_pe, fetched_at, forward_pe_status, forward_eps=None,
    forward_period_end=None, forward_pe_analysts=None, market_source=None,
):
    return {
        "market_price": price,
        "forward_pe": forward_pe,
        "forward_pe_status": forward_pe_status,
        "forward_eps": forward_eps,
        "forward_period_end": forward_period_end,
        "forward_pe_analysts": forward_pe_analysts,
        "market_source": market_source,
        "fetched_at": fetched_at,
    }


def _fmp_forward_eps(ticker):
    """Sum the next four quarterly analyst EPS estimates for an NTM EPS."""
    if not FMP_API_KEY:
        return None
    response = requests.get(
        FMP_ESTIMATES_URL,
        params={"symbol": ticker, "period": "quarter", "page": 0, "limit": 12, "apikey": FMP_API_KEY},
        timeout=8,
    )
    response.raise_for_status()
    rows = response.json()
    if not isinstance(rows, list):
        return None
    today = datetime.now(timezone.utc).date()
    upcoming = []
    for row in rows:
        if not isinstance(row, dict) or not row.get("date"):
            continue
        try:
            period_end = datetime.fromisoformat(str(row["date"])[:10]).date()
        except ValueError:
            continue
        eps = _number(row.get("epsAvg"))
        if period_end > today and eps is not None:
            upcoming.append((period_end, eps, _number(row.get("numAnalystsEps"))))
    upcoming.sort(key=lambda item: item[0])
    next_four = upcoming[:4]
    if len(next_four) != 4:
        return None
    # Reject gaps that suggest the provider omitted one or more quarters.
    if any((next_four[i + 1][0] - next_four[i][0]).days > 120 for i in range(3)):
        return None
    analyst_counts = [int(count) for _, _, count in next_four if count is not None and count > 0]
    return {
        "eps": sum(eps for _, eps, _ in next_four),
        "period_end": next_four[-1][0].isoformat(),
        "analysts": min(analyst_counts) if analyst_counts else None,
    }


def snapshot(ticker):
    if not MARKET_DATA_ENABLED:
        return _snapshot(None, None, None, "unavailable")
    cached = db.get_market_snapshot(ticker)
    cached_pe = None
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
            and (not FMP_API_KEY or cached.get("market_source"))
        ):
            return _snapshot(
                cached.get("market_price"), cached_pe, cached["fetched_at"], cached_status,
                cached.get("forward_eps"), cached.get("forward_period_end"),
                cached.get("forward_pe_analysts"), cached.get("market_source") or "Yahoo Finance",
            )

    price = None
    reported_pe = None
    yahoo_forward_eps = None
    try:
        import yfinance as yf
        info = yf.Ticker(ticker).info or {}
        price = next((value for value in (
            _number(info.get("regularMarketPrice")),
            _number(info.get("currentPrice")),
            _number(info.get("previousClose")),
        ) if value is not None and value > 0), None)
        reported_pe = _number(info.get("forwardPE"))
        yahoo_forward_eps = _number(info.get("forwardEps"))
    except Exception:
        pass

    estimate = None
    if FMP_API_KEY:
        try:
            estimate = _fmp_forward_eps(ticker)
        except (requests.RequestException, ValueError, TypeError):
            estimate = None
    if estimate is not None:
        forward_eps = estimate["eps"]
        if forward_eps <= 0:
            forward_pe = None
            status = "not_meaningful"
        elif price is not None and price > 0:
            forward_pe = price / forward_eps
            status = "available"
        else:
            forward_pe = None
            status = "unavailable"
        source = "FMP analyst consensus"
        period_end = estimate["period_end"]
        analyst_count = estimate["analysts"]
    else:
        forward_eps = yahoo_forward_eps
        if forward_eps is not None and forward_eps <= 0:
            forward_pe = None
            status = "not_meaningful"
        elif price is not None and forward_eps is not None and forward_eps > 0:
            forward_pe = price / forward_eps
            status = "available"
        elif reported_pe is not None and reported_pe > 0:
            forward_pe = reported_pe
            status = "available"
        elif (reported_pe is not None and reported_pe <= 0) or (cached_pe is not None and cached_pe <= 0):
            forward_pe = None
            status = "not_meaningful"
        else:
            forward_pe = None
            status = "unavailable"
        source = "Yahoo Finance (fallback)" if FMP_API_KEY else "Yahoo Finance"
        period_end = None
        analyst_count = None

    fetched_at = now_iso()
    db.save_market_snapshot(
        ticker, price, forward_pe, fetched_at, status,
        forward_eps, period_end, analyst_count, source,
    )
    return _snapshot(price, forward_pe, fetched_at, status, forward_eps, period_end, analyst_count, source)
