from bisect import bisect_right
from datetime import date, datetime, time, timezone, timedelta
import json
import math
import re
from threading import Lock
from zoneinfo import ZoneInfo

import requests

from . import db
from .config import ALPHAVANTAGE_API_KEY, DB_PATH, MARKET_DATA_ENABLED

ALPHAVANTAGE_URL = "https://www.alphavantage.co/query"
YAHOO_SOURCE = "Yahoo Finance"
MARKET_SNAPSHOT_TTL = timedelta(minutes=15)
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
    market_change=None, market_change_percent=None, market_cap=None, trailing_pe=None,
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
        "market_cap": market_cap,
        "trailing_pe": trailing_pe,
        "fetched_at": fetched_at,
    }


def _first_number(row, *keys):
    for key in keys:
        value = _number(row.get(key))
        if value is not None:
            return value
    return None


def _transcript_cache_key(ticker, quarter):
    return f"earnings_transcript:alphavantage:{ticker}:{quarter}"


def earnings_call_transcript(ticker, quarter):
    """Fetch a transcript only when explicitly requested by the dashboard."""
    ticker = ticker.strip().upper()
    quarter = quarter.strip().upper()
    if not re.fullmatch(r"[A-Z0-9.-]{1,10}", ticker):
        raise ValueError("Enter a valid stock ticker.")
    if not re.fullmatch(r"\d{4}Q[1-4]", quarter):
        raise ValueError("Choose a fiscal quarter in YYYYQn format.")

    cache_key = _transcript_cache_key(ticker, quarter)
    cached_text = db.get_meta(cache_key)
    if cached_text:
        try:
            cached = json.loads(cached_text)
            if (
                cached.get("ticker") == ticker
                and cached.get("quarter") == quarter
                and isinstance(cached.get("segments"), list)
                and cached["segments"]
            ):
                return cached
        except (AttributeError, TypeError, ValueError, json.JSONDecodeError):
            pass

    if not ALPHAVANTAGE_API_KEY:
        raise RuntimeError("Alpha Vantage is not configured; add ALPHAVANTAGE_API_KEY to backend/.env.")

    try:
        response = requests.get(
            ALPHAVANTAGE_URL,
            params={
                "function": "EARNINGS_CALL_TRANSCRIPT",
                "symbol": ticker,
                "quarter": quarter,
                "apikey": ALPHAVANTAGE_API_KEY,
            },
            timeout=25,
        )
        response.raise_for_status()
        payload = response.json()
    except requests.RequestException as exc:
        raise RuntimeError("Could not reach Alpha Vantage to fetch the transcript.") from exc
    except ValueError as exc:
        raise RuntimeError("Alpha Vantage returned an invalid transcript response.") from exc

    if isinstance(payload, dict):
        provider_message = next((payload.get(key) for key in ("Error Message", "Note", "Information") if payload.get(key)), None)
        if provider_message:
            raise RuntimeError(str(provider_message))
        raw_segments = payload.get("transcript")
        fiscal_date_ending = payload.get("fiscalDateEnding") or payload.get("fiscal_date_ending")
        reported_date = payload.get("reportedDate") or payload.get("reported_date")
    elif isinstance(payload, list):
        raw_segments = payload
        fiscal_date_ending = None
        reported_date = None
    else:
        raw_segments = None
        fiscal_date_ending = None
        reported_date = None

    if isinstance(raw_segments, str):
        raw_segments = [{"content": raw_segments}]
    segments = []
    if isinstance(raw_segments, list):
        for item in raw_segments:
            if isinstance(item, str):
                item = {"content": item}
            if not isinstance(item, dict):
                continue
            content = next((item.get(key) for key in ("content", "text", "paragraph", "utterance") if item.get(key)), None)
            if content:
                segments.append({
                    "speaker": item.get("speaker") or item.get("speaker_name") or "",
                    "title": item.get("title") or item.get("speaker_title") or "",
                    "content": str(content),
                    "sentiment": item.get("sentiment") or "",
                })

    result = {
        "ticker": ticker,
        "quarter": quarter,
        "fiscal_date_ending": fiscal_date_ending,
        "reported_date": reported_date,
        "segments": segments,
    }
    if segments:
        db.set_meta(cache_key, json.dumps(result, ensure_ascii=False))
    return result


def snapshot(ticker):
    if not MARKET_DATA_ENABLED:
        return _snapshot(None, None, None, "unavailable")

    cached = db.get_market_snapshot(ticker)
    cacheable_sources = {YAHOO_SOURCE}
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
        # Snapshots attributed to a previous provider are refreshed from Yahoo Finance.
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
                cached.get("market_cap"), cached.get("trailing_pe"),
            )

    price = None
    market_change = None
    market_change_percent = None
    market_cap = None
    trailing_pe = None
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
        market_cap = _number(info.get("marketCap"))
        trailing_pe = _number(info.get("trailingPE"))
        reported_pe = _number(info.get("forwardPE"))
        yahoo_forward_eps = _number(info.get("forwardEps"))
    except Exception:
        pass

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
    source = YAHOO_SOURCE
    period_end = None
    analyst_count = None

    fetched_at = now_iso()
    db.save_market_snapshot(
        ticker, price, forward_pe, fetched_at, status,
        forward_eps, period_end, analyst_count, source,
        market_change, market_change_percent, market_cap, trailing_pe,
    )
    return _snapshot(
        price, forward_pe, fetched_at, status, forward_eps, period_end,
        analyst_count, source, market_change, market_change_percent, market_cap, trailing_pe,
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
