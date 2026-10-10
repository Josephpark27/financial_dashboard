from bisect import bisect_right
from datetime import date, datetime, time, timezone, timedelta
import json
import math
from threading import Lock
from zoneinfo import ZoneInfo

import requests

from . import db
from .config import ALPHAVANTAGE_API_KEY, DB_PATH, MARKET_DATA_ENABLED

ALPHAVANTAGE_URL = "https://www.alphavantage.co/query"
ALPHAVANTAGE_SOURCE = "Alpha Vantage analyst consensus"
YAHOO_SOURCE = "Yahoo Finance"
YAHOO_AV_UNAVAILABLE_SOURCE = "Yahoo Finance (Alpha Vantage unavailable)"
MARKET_SNAPSHOT_TTL = timedelta(minutes=15)
ESTIMATE_CACHE_TTL = timedelta(hours=24)
EARNINGS_DATE_CACHE_TTL = timedelta(hours=24)
HISTORICAL_CLOSE_CACHE_TTL = timedelta(hours=24)
EMPTY_HISTORICAL_CLOSE_CACHE_TTL = timedelta(minutes=15)
_YFINANCE_CACHE_LOCK = Lock()
_YFINANCE_CACHE_CONFIGURED = False


def now_iso():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _number(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _yfinance_client():
    global _YFINANCE_CACHE_CONFIGURED
    import yfinance as yf

    # yfinance's default user-cache location can be unavailable in packaged
    # or sandboxed runs. Keep its SQLite caches beside the dashboard database.
    if not _YFINANCE_CACHE_CONFIGURED:
        with _YFINANCE_CACHE_LOCK:
            if not _YFINANCE_CACHE_CONFIGURED:
                yf.set_tz_cache_location(str(DB_PATH.parent / "yfinance-cache"))
                _YFINANCE_CACHE_CONFIGURED = True
    return yf


def _historical_closes(ticker, first_period_end):
    """Load and cache split-adjusted daily closes needed for historical P/E."""
    ticker = ticker.upper()
    fetch_start = first_period_end - timedelta(days=10)
    cache_key = f"market_history:yahoo:v2:{ticker}"
    cached_text = db.get_meta(cache_key)
    if cached_text:
        try:
            cached = json.loads(cached_text)
            cached_at = datetime.fromisoformat(cached["cached_at"].replace("Z", "+00:00"))
            prices = cached.get("prices", [])
            ttl = HISTORICAL_CLOSE_CACHE_TTL if prices else EMPTY_HISTORICAL_CLOSE_CACHE_TTL
            cache_covers_start = prices and prices[0]["date"] <= fetch_start.isoformat()
            if datetime.now(timezone.utc) - cached_at < ttl and (not prices or cache_covers_start):
                return prices
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            pass

    prices = []
    try:
        yf = _yfinance_client()
        history = yf.Ticker(ticker).history(
            start=fetch_start.isoformat(),
            end=(date.today() + timedelta(days=1)).isoformat(),
            interval="1d", auto_adjust=False, actions=False, timeout=10,
        )
        if history is not None and not history.empty and "Close" in history:
            for stamp, raw_close in history["Close"].dropna().items():
                close = _number(raw_close)
                if close is not None and close > 0:
                    prices.append({"date": stamp.date().isoformat(), "close": close})
    except Exception:
        # Historical valuation is optional and should not break the dashboard.
        prices = []

    prices.sort(key=lambda item: item["date"])
    db.set_meta(cache_key, json.dumps({"cached_at": now_iso(), "prices": prices}))
    return prices


def historical_pe(ticker, eps_rows):
    """Calculate quarter-end trailing P/E from Yahoo closes and SEC TTM EPS."""
    if not MARKET_DATA_ENABLED:
        return []
    eligible = [
        (row, _number(row.get("ttm_value")))
        for row in eps_rows
        if _number(row.get("ttm_value")) is not None and _number(row.get("ttm_value")) > 0
    ]
    if not eligible:
        return []

    period_ends = [date.fromisoformat(row["period_end"]) for row, _ in eligible]
    closes = _historical_closes(ticker, min(period_ends))
    if not closes:
        return []
    close_dates = [row["date"] for row in closes]
    output = []
    for (row, ttm_eps), period_end in zip(eligible, period_ends):
        index = bisect_right(close_dates, period_end.isoformat()) - 1
        if index < 0:
            continue
        close_date = date.fromisoformat(close_dates[index])
        if (period_end - close_date).days > 7:
            continue
        price = closes[index]["close"]
        output.append({
            "period_end": period_end.isoformat(),
            "fiscal_period": row.get("fiscal_period"),
            "price": price,
            "ttm_eps": ttm_eps,
            "pe": price / ttm_eps,
        })
    return output


def _snapshot(
    price, forward_pe, fetched_at, forward_pe_status, forward_eps=None,
    forward_period_end=None, forward_pe_analysts=None, market_source=None,
    market_change=None, market_change_percent=None,
):
    return {
        "market_price": price,
        "forward_pe": forward_pe,
        "forward_pe_status": forward_pe_status,
        "forward_eps": forward_eps,
        "forward_period_end": forward_period_end,
        "forward_pe_analysts": forward_pe_analysts,
        "market_source": market_source,
        "market_change": market_change,
        "market_change_percent": market_change_percent,
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
                cached.get("market_change"), cached.get("market_change_percent"),
            )

    price = None
    market_change = None
    market_change_percent = None
    reported_pe = None
    yahoo_forward_eps = None
    try:
        yf = _yfinance_client()
        info = yf.Ticker(ticker).info or {}
        current_price = next((value for value in (
            _number(info.get("regularMarketPrice")),
            _number(info.get("currentPrice")),
        ) if value is not None and value > 0), None)
        previous_close = _first_number(
            info, "regularMarketPreviousClose", "previousClose",
        )
        price = current_price or (previous_close if previous_close and previous_close > 0 else None)
        market_change = _first_number(info, "regularMarketChange", "regularMarketChangeAmount")
        reported_change_percent = _first_number(
            info, "regularMarketChangePercent", "regularMarketChangePercentChange",
        )
        if market_change is None and current_price is not None and previous_close:
            market_change = current_price - previous_close
        market_change_percent = reported_change_percent
        if market_change is not None and previous_close:
            market_change_percent = market_change / previous_close * 100
        elif market_change_percent is not None and previous_close:
            market_change = previous_close * market_change_percent / 100
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
        market_change, market_change_percent,
    )
    return _snapshot(
        price, forward_pe, fetched_at, status, forward_eps, period_end,
        analyst_count, source, market_change, market_change_percent,
    )


def earnings_event(ticker):
    """Return the next announced earnings date, or the latest past date."""
    if not MARKET_DATA_ENABLED:
        return None, None

    ticker = ticker.upper()
    cache_key = f"market_earnings_date:yahoo:v2:{ticker}"
    cached_text = db.get_meta(cache_key)
    if cached_text:
        try:
            cached = json.loads(cached_text)
            cached_at = datetime.fromisoformat(cached["cached_at"].replace("Z", "+00:00"))
            if datetime.now(timezone.utc) - cached_at < EARNINGS_DATE_CACHE_TTL:
                return _earnings_event_time(ticker, cached.get("date")), cached.get("type")
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            pass

    earnings_date = None
    earnings_date_type = None
    try:
        events = _yfinance_client().Ticker(ticker).get_earnings_dates(limit=12)
        now = datetime.now(timezone.utc)
        event_dates = set()
        for value in getattr(events, "index", []):
            try:
                timestamp = value.to_pydatetime() if hasattr(value, "to_pydatetime") else value
                if not isinstance(timestamp, datetime):
                    timestamp = datetime.fromisoformat(str(timestamp))
                if timestamp.tzinfo is None:
                    timestamp = timestamp.replace(tzinfo=timezone.utc)
                event_dates.add(timestamp)
            except (TypeError, ValueError):
                continue
        event_dates = sorted(event_dates)
        upcoming = [value for value in event_dates if value >= now]
        previous = [value for value in event_dates if value < now]
        selected_event = None
        if upcoming:
            selected_event, earnings_date_type = upcoming[0], "next"
        elif previous:
            selected_event, earnings_date_type = previous[-1], "last"
        if selected_event is not None:
            earnings_date = _earnings_event_time(ticker, selected_event.isoformat())
    except Exception:
        pass

    try:
        db.set_meta(cache_key, json.dumps({
            "cached_at": now_iso(),
            "date": earnings_date,
            "type": earnings_date_type,
        }))
    except Exception:
        pass
    return earnings_date, earnings_date_type


def _earnings_event_time(ticker, value):
    if not value or ticker != "AAPL":
        return value
    try:
        event = datetime.fromisoformat(value.replace("Z", "+00:00"))
        # Yahoo's earnings event timestamp differs from Apple's 2:00 PM Pacific
        # conference call time. Preserve the announced event date and use the call time.
        return datetime.combine(
            event.date(), time(14, 0), ZoneInfo("America/Los_Angeles"),
        ).isoformat()
    except (AttributeError, TypeError, ValueError):
        return value
