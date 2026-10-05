from datetime import datetime, timezone, timedelta
import json
import math

import requests

from . import db
from .config import ALPHAVANTAGE_API_KEY, MARKET_DATA_ENABLED

ALPHAVANTAGE_URL = "https://www.alphavantage.co/query"
ALPHAVANTAGE_SOURCE = "Alpha Vantage analyst consensus"
YAHOO_SOURCE = "Yahoo Finance"
YAHOO_AV_UNAVAILABLE_SOURCE = "Yahoo Finance (Alpha Vantage unavailable)"
MARKET_SNAPSHOT_TTL = timedelta(minutes=15)
ESTIMATE_CACHE_TTL = timedelta(hours=24)


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


def _first_number(row, *keys):
    for key in keys:
        value = _number(row.get(key))
        if value is not None:
            return value
    return None


def _estimate_rows(payload):
    """Accept the provider's quarterly collection and common SDK response shapes."""
    if isinstance(payload, list):
        return payload
    if not isinstance(payload, dict):
        return []

    for key in (
        "quarterlyEstimates", "quarterly_estimates", "quarterly estimates", "quarterly",
    ):
        rows = payload.get(key)
        if isinstance(rows, list):
            return rows

    rows = payload.get("estimates")
    if isinstance(rows, list):
        return [
            row for row in rows
            if isinstance(row, dict)
            and (
                not row.get("horizon")
                or "quarter" in str(row.get("horizon", "")).lower()
            )
        ]
    return []


def _sum_next_four_quarters(rows):
    today = datetime.now(timezone.utc).date()
    by_period = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        period_text = next((row.get(key) for key in (
            "date", "fiscalDateEnding", "fiscal_date_ending", "periodEnd", "period_end",
        ) if row.get(key)), None)
        if not period_text:
            continue
        try:
            period_end = datetime.fromisoformat(str(period_text)[:10]).date()
        except ValueError:
            continue
        eps = _first_number(
            row, "epsEstimateAverage", "eps_estimate_average", "epsAvg", "epsAverage",
            "estimatedEPS", "estimatedEps", "epsEstimated",
        )
        if period_end <= today or eps is None:
            continue
        analysts = _first_number(
            row, "epsEstimateAnalystCount", "eps_estimate_analyst_count", "numAnalystsEps",
            "numberOfAnalysts", "analystCount", "analyst_count",
        )
        by_period[period_end] = (eps, analysts)

    upcoming = sorted(by_period.items())[:4]
    if len(upcoming) != 4:
        return None
    # Do not call a partial or gapped forecast a next-twelve-month estimate.
    if any((upcoming[i + 1][0] - upcoming[i][0]).days > 120 for i in range(3)):
        return None

    analyst_counts = [int(count) for _, (_, count) in upcoming if count is not None and count > 0]
    return {
        "eps": sum(eps for _, (eps, _) in upcoming),
        "period_end": upcoming[-1][0].isoformat(),
        "analysts": min(analyst_counts) if analyst_counts else None,
    }


def _estimate_cache_key(ticker):
    return f"market_estimate:alphavantage:{ticker.upper()}"


def _read_cached_estimate(ticker):
    cached_text = db.get_meta(_estimate_cache_key(ticker))
    if not cached_text:
        return False, None
    try:
        cached = json.loads(cached_text)
        cached_at = datetime.fromisoformat(cached["cached_at"].replace("Z", "+00:00"))
        if datetime.now(timezone.utc) - cached_at >= ESTIMATE_CACHE_TTL:
            return False, None
        return True, cached.get("estimate")
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return False, None


def _write_cached_estimate(ticker, estimate):
    db.set_meta(
        _estimate_cache_key(ticker),
        json.dumps({"cached_at": now_iso(), "estimate": estimate}),
    )


def _alphavantage_forward_eps(ticker):
    """Sum the next four quarterly consensus EPS estimates for an NTM EPS."""
    if not ALPHAVANTAGE_API_KEY:
        return None

    cache_hit, estimate = _read_cached_estimate(ticker)
    if cache_hit:
        return estimate

    try:
        response = requests.get(
            ALPHAVANTAGE_URL,
            params={
                "function": "EARNINGS_ESTIMATES",
                "symbol": ticker,
                "apikey": ALPHAVANTAGE_API_KEY,
            },
            timeout=8,
        )
        response.raise_for_status()
        payload = response.json()
        # Alpha Vantage can return quota, entitlement, and validation messages
        # in a successful HTTP response. Cache those as unavailable too, so a
        # page refresh cannot consume the daily free-call allowance repeatedly.
        if isinstance(payload, dict) and any(
            key in payload for key in ("Error Message", "Note", "Information")
        ):
            _write_cached_estimate(ticker, None)
            return None
        estimate = _sum_next_four_quarters(_estimate_rows(payload))
        _write_cached_estimate(ticker, estimate)
        return estimate
    except (requests.RequestException, ValueError, TypeError):
        # Network and response failures also get a short-lived negative cache.
        _write_cached_estimate(ticker, None)
        return None


def snapshot(ticker):
    if not MARKET_DATA_ENABLED:
        return _snapshot(None, None, None, "unavailable")

    cached = db.get_market_snapshot(ticker)
    cacheable_sources = {
        ALPHAVANTAGE_SOURCE, YAHOO_SOURCE, YAHOO_AV_UNAVAILABLE_SOURCE,
    }
    cached_pe = None
    if cached:
        fetched = datetime.fromisoformat(cached["fetched_at"].replace("Z", "+00:00"))
        cached_pe = _number(cached.get("forward_pe"))
        cached_source = cached.get("market_source")
        cached_status = cached.get("forward_pe_status")
        if cached_status is None:
            cached_status = "available" if cached_pe is not None and cached_pe > 0 else (
                "not_meaningful" if cached_pe is not None else "unavailable"
            )
        # FMP snapshots are intentionally invalidated after removing that
        # provider, and old Yahoo snapshots are refreshed to update attribution.
        if (
            cached_source in cacheable_sources
            and (cached_pe is None or cached_pe > 0)
            and datetime.now(timezone.utc) - fetched < MARKET_SNAPSHOT_TTL
        ):
            return _snapshot(
                cached.get("market_price"), cached_pe, cached["fetched_at"], cached_status,
                cached.get("forward_eps"), cached.get("forward_period_end"),
                cached.get("forward_pe_analysts"), cached_source,
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

    estimate = _alphavantage_forward_eps(ticker)
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
        source = ALPHAVANTAGE_SOURCE
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
        elif reported_pe is not None and reported_pe <= 0:
            forward_pe = None
            status = "not_meaningful"
        else:
            forward_pe = None
            status = "unavailable"
        source = YAHOO_AV_UNAVAILABLE_SOURCE if ALPHAVANTAGE_API_KEY else YAHOO_SOURCE
        period_end = None
        analyst_count = None

    fetched_at = now_iso()
    db.save_market_snapshot(
        ticker, price, forward_pe, fetched_at, status,
        forward_eps, period_end, analyst_count, source,
    )
    return _snapshot(price, forward_pe, fetched_at, status, forward_eps, period_end, analyst_count, source)
