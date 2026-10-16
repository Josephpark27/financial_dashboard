import re
from datetime import date, datetime, timedelta, timezone
from html import unescape
from io import StringIO

import pandas as pd
import requests

from . import db
from .config import SEC_USER_AGENT

HEADERS = {"User-Agent": SEC_USER_AGENT, "Accept-Encoding": "gzip, deflate"}
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
ARCHIVES_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{accession}/"
FALLBACK_PARSER_VERSION = 5
FALLBACK_CACHE_TTL = timedelta(hours=24)
MONTHS = {name: idx for idx, name in enumerate(
    ["January", "February", "March", "April", "May", "June", "July",
     "August", "September", "October", "November", "December"], start=1)}

_QUARTER_HDR = re.compile(
    r"(three)\s+months\s+ended\s+"
    r"(January|February|March|April|May|June|July|August|September|October|November|December)"
    r"\s*(\d{1,2}),?", re.I)
_QUARTER_LABEL_HDR = re.compile(r"\bQ([1-4])\s+(20\d{2})\b", re.I)
_STATEMENT_DATE = re.compile(
    r"\b(January|February|March|April|May|June|July|August|September|October|November|December)"
    r"\s+(\d{1,2}),?\s+(20\d{2})\b", re.I,
)


def _text(value):
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    return str(value).replace("\xa0", " ")


def _label(value):
    return re.sub(r"\s+", " ", _text(value)).strip()


def _parse_number(value):
    text = _label(value).replace("$", "").replace(",", "")
    if not text or text in {"-", "--"}:
        return None
    negative = text.startswith("(")
    cleaned = text.replace("(", "").replace(")", "").strip()
    if not re.fullmatch(r"-?\d+(\.\d+)?", cleaned):
        return None
    number = float(cleaned)
    return -number if negative else number


def _period_start(end):
    month = (end.month - 1) // 3 * 3 + 1
    return date(end.year, month, 1)


def fallback_needs_refresh(cik):
    cached = db.get_facts(-cik)
    if not cached or cached["payload"].get("parser_version") != FALLBACK_PARSER_VERSION:
        return True
    try:
        fetched = datetime.fromisoformat(cached["fetched_at"].replace("Z", "+00:00"))
        if fetched.tzinfo is None:
            fetched = fetched.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return True
    return datetime.now(timezone.utc) - fetched >= FALLBACK_CACHE_TTL


def _quarter_columns(table):
    if table.shape[0] < 3:
        return []
    header_text = " ".join(_label(v) for row in table.iloc[:2].values.tolist() for v in row)
    quarter_labels = _quarter_label_columns(table)
    if not _QUARTER_HDR.search(header_text) and not quarter_labels:
        return []
    if quarter_labels:
        return [(column, year) for column, year, _ in quarter_labels]
    year_cells = []
    for idx, value in enumerate(table.iloc[1].tolist()):
        text = _text(value)
        if re.fullmatch(r"20\d\d", text) and (not year_cells or year_cells[-1][1] != int(text)):
            year_cells.append((idx, int(text)))
    if len(year_cells) >= 2:
        return year_cells[:2]
    return []


def _quarter_label_columns(table):
    columns = []
    for column in range(table.shape[1]):
        header = " ".join(_text(value) for value in table.iloc[:3, column].tolist())
        match = _QUARTER_LABEL_HDR.search(header)
        if match:
            columns.append((column, int(match.group(2)), f"Q{match.group(1)}"))
    return columns


def _extract_metric(table, pattern, year_cols):
    for row in table.values[2:]:
        if not re.search(pattern, _label(row[0]), re.I):
            continue
        found = []
        for year_col, year in year_cols:
            value = None
            for col in range(year_col, min(year_col + 3, len(row))):
                value = _parse_number(row[col])
                if value is not None:
                    break
            found.append((year, value))
        if any(v is not None for _, v in found):
            yield from ((year, value) for year, value in found if value is not None)
            return


def _exhibits_from_filing(cik, accession):
    url = ARCHIVES_URL.format(cik=cik, accession=accession)
    index = requests.get(f"{url}index.json", headers=HEADERS, timeout=30).json()
    documents = [
        f"{url}{item['name']}"
        for item in index["directory"]["item"]
        if re.search(r"\.html?$", item["name"], re.I)
    ]
    likely = [
        document for document in documents
        if re.search(
            r"ex99|press.?release|financial.?results?|quarterly.?results?",
            document.rsplit("/", 1)[-1], re.I,
        )
    ]
    return likely or documents


def _read_exhibit(url):
    try:
        response = requests.get(url, headers=HEADERS, timeout=30)
        response.raise_for_status()
        html = response.text
        tables = pd.read_html(StringIO(html))
        balance_tables = pd.read_html(StringIO(html), header=None)
    except Exception:
        return None
    return {"html": html, "tables": tables, "balance_tables": balance_tables}


def _facts_from_exhibit(url, report_date=None, document=None):
    document = document or _read_exhibit(url)
    if not document:
        return None
    tables = document["tables"]
    best = None
    for table in tables:
        if table.shape[0] < 3:
            continue
        header_text = " ".join(_label(v) for row in table.iloc[:2].values.tolist() for v in row)
        quarter_match = _QUARTER_HDR.search(header_text)
        quarter_labels = _quarter_label_columns(table)
        if not quarter_match and not quarter_labels:
            continue
        columns = _quarter_columns(table)
        if len(columns) < 2:
            continue
        revenue = []
        for pattern in (
            r"^Total\s+net\s+sales$", r"^Total\s+revenues?$",
            r"^Net\s+sales$", r"^Revenues?$",
        ):
            revenue = list(_extract_metric(table, pattern, [columns[-1]]))
            if revenue:
                break
        income = list(_extract_metric(
            table,
            r"^Net\s+(?:income\s*/\s*\(loss\)|\(loss\)\s*/\s*income|income|loss)"
            r"(?:\s+from continuing operations)?$",
            [columns[-1]],
        ))
        if not revenue:
            continue
        if quarter_match:
            _, month_name, day = quarter_match.groups()
            end = date(columns[-1][1], MONTHS[month_name.capitalize()], int(day))
        elif report_date:
            try:
                end = datetime.fromisoformat(report_date[:10]).date()
            except (TypeError, ValueError):
                end = None
        else:
            end = None
        if end is None and quarter_labels:
            _, year, fiscal_quarter = quarter_labels[-1]
            month = int(fiscal_quarter[1:]) * 3
            end = date(year, month, 1) + timedelta(days=31)
            end = date(end.year, end.month, 1) - timedelta(days=1)
        if end is None:
            continue
        currency = (
            "EUR" if re.search(r"€|\beuros?\b|\bEUR\b", document["html"], re.I)
            else "USD"
        )
        result = {
            "end": end,
            "revenue": revenue,
            "income": income,
            "url": url,
            "currency": currency,
        }
        if income:
            return result
        if best is None:
            best = result
    return best


def _period_end_from_header(value):
    match = _STATEMENT_DATE.search(_label(value))
    if not match:
        return None
    month, day, year = match.groups()
    try:
        return date(int(year), MONTHS[month.capitalize()], int(day))
    except ValueError:
        return None


def _balance_period_columns(table):
    """Map the date columns in an SEC balance-sheet table to their period ends."""
    header_rows = min(12, table.shape[0])
    columns = {}
    for column in range(table.shape[1]):
        header = " ".join(_label(value) for value in table.iloc[:header_rows, column].tolist())
        end = _period_end_from_header(header)
        if end:
            columns[column] = end
    if len(set(columns.values())) >= 2:
        return columns

    # Some SEC tables put the month/day and year in separate header cells.
    # If that prevents a direct column match, pair the statement dates with
    # the final numeric columns in the total-assets row.
    header_text = " ".join(
        _label(value) for row in table.iloc[:header_rows].values.tolist() for value in row
    )
    dates = []
    for match in _STATEMENT_DATE.finditer(header_text):
        month, day, year = match.groups()
        try:
            end = date(int(year), MONTHS[month.capitalize()], int(day))
        except ValueError:
            continue
        if end not in dates:
            dates.append(end)
    if len(dates) < 2:
        return columns

    asset_row = next((
        row for row in table.values
        if re.fullmatch(r"total\s+assets", _label(row[0]), re.I)
    ), None)
    if asset_row is None:
        return columns
    value_columns = [
        index for index, value in enumerate(asset_row)
        if _parse_number(value) is not None or _label(value) in {"—", "–", "-", "--"}
    ]
    if len(value_columns) >= len(dates):
        return dict(zip(value_columns[-len(dates):], dates))
    return columns


def _statement_value(row, column):
    value = _parse_number(row[column]) if column < len(row) else None
    if value is not None:
        return value
    if column < len(row) and _label(row[column]) in {"—", "–", "-", "--"}:
        return 0.0
    return None


def _row_period_values(row, period_columns):
    values = {end: _statement_value(row, column) for column, end in period_columns.items()}
    if any(value is not None for value in values.values()):
        return values

    numeric_values = []
    for value in row[1:]:
        parsed = _parse_number(value)
        if parsed is not None:
            numeric_values.append(parsed)
        elif _label(value) in {"—", "–", "-", "--"}:
            numeric_values.append(0.0)
    ends = list(dict.fromkeys(period_columns.values()))
    if len(numeric_values) >= len(ends):
        return dict(zip(ends, numeric_values[-len(ends):]))
    return values


def _balance_sheet_from_exhibit(url, filed=None, document=None):
    """Read reported balance-sheet values from the quarterly 6-K exhibit."""
    document = document or _read_exhibit(url)
    if not document:
        return []

    points = {}
    plain_html = unescape(re.sub(r"<[^>]+>", " ", document["html"]))
    title_match = re.search(r"balance\s+sheets?", plain_html, re.I)
    statement_context = (
        plain_html[title_match.start():title_match.start() + 1_200] if title_match else ""
    )
    for table in document["balance_tables"]:
        rows = table.values.tolist()
        labels = {_label(row[0]).lower() for row in rows if row}
        if not {"total assets", "total liabilities"}.issubset(labels):
            continue
        period_columns = _balance_period_columns(table)
        if not period_columns:
            continue

        units_header = " ".join(_label(value) for row in rows[:7] for value in row)
        units_header += " " + statement_context
        if re.search(r"\b(thousands|\$\s*in\s*thousands)\b", units_header, re.I):
            scale = 1_000
        elif re.search(r"\b(billions|\$\s*in\s*billions)\b", units_header, re.I):
            scale = 1_000_000_000
        elif re.search(r"\bmillions\b", units_header, re.I):
            scale = 1_000_000
        else:
            # Nebius presents SEC interim statements in millions when the unit
            # line is omitted from the HTML table itself.
            scale = 1_000_000
        currency = "EUR" if re.search(r"\bEUR\b|euros?|€", units_header, re.I) else "USD"
        for end in set(period_columns.values()):
            points[end] = {
                "period_end": end.isoformat(), "filed": filed, "url": url,
                "currency": currency,
            }

        row_rules = {
            "assets": re.compile(r"^total\s+assets$", re.I),
            "liabilities": re.compile(r"^total\s+liabilities$", re.I),
            "cash": re.compile(r"^cash(?:\s+and\s+cash\s+equivalents)?$", re.I),
            "marketable_securities": re.compile(
                r"^(?!.*non[ -]?marketable).*(?:marketable securities|short[ -]term investments)$", re.I,
            ),
            "current_debt": re.compile(
                r"^(?:debt[, ]+current|current portion of (?:long[ -]term )?debt|long[ -]term debt[, ]+current)$", re.I,
            ),
            "noncurrent_debt": re.compile(
                r"^(?:debt[, ]+non[ -]current|long[ -]term debt[, ]+non[ -]current)$", re.I,
            ),
            "total_debt": re.compile(r"^total debt$", re.I),
        }
        for row in rows:
            label = _label(row[0])
            for field, pattern in row_rules.items():
                if not pattern.fullmatch(label):
                    continue
                for end, value in _row_period_values(row, period_columns).items():
                    if value is not None:
                        points[end][field] = value * scale
                break

        for point in points.values():
            if "total_debt" in point:
                point["debt"] = point["total_debt"]
            elif "current_debt" in point or "noncurrent_debt" in point:
                point["debt"] = point.get("current_debt", 0.0) + point.get("noncurrent_debt", 0.0)
        if any(any(key in point for key in ("assets", "liabilities", "cash", "debt")) for point in points.values()):
            return list(points.values())
    return []


def six_k_fallback(cik, kind):
    cached = db.get_facts(cik * -1)
    if not cached:
        raise ValueError(f"No cached 6-K fallback data for CIK {cik}.")
    payload = cached["payload"]
    if payload.get("parser_version") != FALLBACK_PARSER_VERSION:
        raise ValueError(f"Cached 6-K fallback data for CIK {cik} needs to be refreshed.")
    points = payload.get("points", [])
    key = "revenue" if kind == "revenue" else "net_income"
    concept = "6-K:Revenues" if kind == "revenue" else "6-K:NetIncomeLoss"
    facts_rows = []
    for point in points:
        value = point.get(key)
        if value is None:
            continue
        end = datetime.fromisoformat(point["period_end"]).date()
        facts_rows.append({
            "start": point["start"], "end": point["period_end"],
            "val": value * 1_000_000, "fy": end.year,
            "fp": f"Q{(end.month - 1) // 3 + 1}", "filed": point.get("filed"),
            "currency": point.get("currency", "USD"),
        })
    if not facts_rows:
        raise ValueError(f"Could not identify quarterly {kind} facts in the SEC data.")
    return concept, facts_rows


def six_k_balance_sheet_fallback(cik):
    cached = db.get_facts(cik * -1)
    if not cached or cached["payload"].get("parser_version") != FALLBACK_PARSER_VERSION:
        return []
    return cached["payload"].get("balance_sheet_points", [])


def refresh_six_k_fallback(cik, years_back):
    response = requests.get(SUBMISSIONS_URL.format(cik=cik), headers=HEADERS, timeout=30)
    response.raise_for_status()
    recent = response.json()["filings"]["recent"]
    cutoff = date.today() - timedelta(days=365 * years_back) - timedelta(days=500)
    history_limit = years_back * 4 + 4
    points = {}
    balance_sheet_points = {}
    scanned = 0
    report_dates = recent.get("reportDate", [])
    for idx, form in enumerate(recent["form"]):
        filed = recent["filingDate"][idx]
        report_date = report_dates[idx] if idx < len(report_dates) else ""
        try:
            filed_date = datetime.fromisoformat(filed).date()
        except Exception:
            continue
        if filed_date < cutoff or len(points) >= history_limit or scanned >= 200:
            break
        if form != "6-K":
            continue
        scanned += 1
        accession = recent["accessionNumber"][idx].replace("-", "")
        try:
            exhibits = _exhibits_from_filing(cik, accession)
        except Exception:
            continue
        for url in exhibits:
            document = _read_exhibit(url)
            if not document:
                continue
            result = _facts_from_exhibit(url, report_date, document)
            if result and result["end"] >= cutoff and result["end"] not in points:
                points[result["end"]] = {
                    "period_end": result["end"].isoformat(),
                    "start": _period_start(result["end"]).isoformat(),
                    "revenue": next((v for _, v in result["revenue"]), None),
                    "net_income": next((v for _, v in result["income"]), None),
                    "currency": result["currency"],
                    "filed": filed, "url": url,
                }
            for balance_point in _balance_sheet_from_exhibit(url, filed, document):
                end = balance_point["period_end"]
                if date.fromisoformat(end) < cutoff:
                    continue
                previous = balance_sheet_points.get(end, {})
                # A newer filing's comparative statements take precedence, while
                # omitted rows from an exhibit retain values already collected.
                if filed >= previous.get("filed", ""):
                    balance_sheet_points[end] = {**previous, **balance_point}
    payload = {
        "parser_version": FALLBACK_PARSER_VERSION,
        "points": [points[end] for end in sorted(points)],
        "balance_sheet_points": [balance_sheet_points[end] for end in sorted(balance_sheet_points)],
    }
    db.save_facts(cik * -1, payload, db_now())
    return payload


def db_now():
    from .sec import now_iso
    return now_iso()
