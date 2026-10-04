import re
from datetime import date, datetime, timedelta, timezone
from io import StringIO

import pandas as pd
import requests

from . import db
from .config import SEC_USER_AGENT

HEADERS = {"User-Agent": SEC_USER_AGENT, "Accept-Encoding": "gzip, deflate"}
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
ARCHIVES_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{accession}/"
FALLBACK_PARSER_VERSION = 3
FALLBACK_CACHE_TTL = timedelta(hours=24)
MONTHS = {name: idx for idx, name in enumerate(
    ["January", "February", "March", "April", "May", "June", "July",
     "August", "September", "October", "November", "December"], start=1)}

_QUARTER_HDR = re.compile(
    r"(three)\s+months\s+ended\s+"
    r"(January|February|March|April|May|June|July|August|September|October|November|December)"
    r"\s*(\d{1,2}),?", re.I)


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
    if not _QUARTER_HDR.search(header_text):
        return []
    year_cells = []
    for idx, value in enumerate(table.iloc[1].tolist()):
        text = _text(value)
        if re.fullmatch(r"20\d\d", text) and (not year_cells or year_cells[-1][1] != int(text)):
            year_cells.append((idx, int(text)))
    if len(year_cells) < 2:
        return []
    return year_cells[:2]


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
    return [
        f"{url}{item['name']}"
        for item in index["directory"]["item"]
        if re.search(r"ex99(?:-?1|d1).*\.htm$", item["name"], re.I)
    ]


def _facts_from_exhibit(url):
    try:
        response = requests.get(url, headers=HEADERS, timeout=30)
        tables = pd.read_html(StringIO(response.text))
    except Exception:
        return None
    best = None
    for table in tables:
        if table.shape[0] < 3:
            continue
        header_text = " ".join(_label(v) for row in table.iloc[:2].values.tolist() for v in row)
        quarter_match = _QUARTER_HDR.search(header_text)
        if not quarter_match:
            continue
        columns = _quarter_columns(table)
        if len(columns) < 2:
            continue
        latest_col, latest_year = columns[-1]
        revenue = list(_extract_metric(table, r"^Revenues?$", [columns[-1]]))
        income = list(_extract_metric(
            table,
            r"^Net\s+(?:income\s*/\s*\(loss\)|\(loss\)\s*/\s*income|income|loss)"
            r"(?:\s+from continuing operations)?$",
            [columns[-1]],
        ))
        if not revenue:
            continue
        _, month_name, day = quarter_match.groups()
        result = {
            "end": date(latest_year, MONTHS[month_name.capitalize()], int(day)),
            "revenue": revenue,
            "income": income,
            "url": url,
        }
        if income:
            return result
        if best is None:
            best = result
    return best


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
        })
    if not facts_rows:
        raise ValueError(f"Could not identify quarterly {kind} facts in the SEC data.")
    return concept, facts_rows


def refresh_six_k_fallback(cik, years_back):
    response = requests.get(SUBMISSIONS_URL.format(cik=cik), headers=HEADERS, timeout=30)
    response.raise_for_status()
    recent = response.json()["filings"]["recent"]
    cutoff = date.today() - timedelta(days=365 * years_back) - timedelta(days=500)
    history_limit = years_back * 4 + 4
    points = {}
    scanned = 0
    for idx, form in enumerate(recent["form"]):
        filed = recent["filingDate"][idx]
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
            result = _facts_from_exhibit(url)
            if not result or result["end"] < cutoff or result["end"] in points:
                continue
            points[result["end"]] = {
                "period_end": result["end"].isoformat(),
                "start": _period_start(result["end"]).isoformat(),
                "revenue": next((v for _, v in result["revenue"]), None),
                "net_income": next((v for _, v in result["income"]), None),
                "filed": filed, "url": url,
            }
            break
    payload = {
        "parser_version": FALLBACK_PARSER_VERSION,
        "points": [points[end] for end in sorted(points)],
    }
    db.save_facts(cik * -1, payload, db_now())
    return payload


def db_now():
    from .sec import now_iso
    return now_iso()
