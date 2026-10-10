from datetime import datetime, timezone, timedelta
import hashlib
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
from urllib.parse import urlparse
import time

import requests

from .config import FACTS_URL, SEC_USER_AGENT, TICKERS_URL
from . import db

SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
ARCHIVES_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{accession}/{document}"
_SUBMISSIONS_CACHE = {}
_SUBMISSIONS_CACHE_TTL = timedelta(hours=24)
SEC_FILING_PATH = re.compile(r"^/Archives/edgar/data/\d+/\d{18}/[A-Za-z0-9._-]+\.html?$", re.I)

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

def quarterly_filing_links(cik, periods):
    """Find the SEC filing for each quarter-end using the submission report date."""
    cached = _SUBMISSIONS_CACHE.get(cik)
    if cached and datetime.now(timezone.utc) - cached[0] < _SUBMISSIONS_CACHE_TTL:
        recent = cached[1]
    else:
        try:
            response = requests.get(SUBMISSIONS_URL.format(cik=cik), headers=HEADERS, timeout=30)
            response.raise_for_status()
            recent = response.json().get("filings", {}).get("recent", {})
        except requests.RequestException:
            return []
        _SUBMISSIONS_CACHE[cik] = (datetime.now(timezone.utc), recent)

    forms = recent.get("form", [])
    report_dates = recent.get("reportDate", [])
    filing_dates = recent.get("filingDate", [])
    accessions = recent.get("accessionNumber", [])
    documents = recent.get("primaryDocument", [])
    matches = []
    for period in periods:
        end = period.get("period_end")
        fiscal_period = period.get("fiscal_period") or ""
        filed = period.get("filed")
        expected_form = "10-K" if fiscal_period.endswith("Q4") else "10-Q"
        candidates = []
        for idx, form in enumerate(forms):
            if form not in {expected_form, f"{expected_form}/A"}:
                continue
            report_date = report_dates[idx] if idx < len(report_dates) else ""
            filing_date = filing_dates[idx] if idx < len(filing_dates) else ""
            if report_date != end and not (filed and filing_date == filed):
                continue
            accession = accessions[idx] if idx < len(accessions) else ""
            document = documents[idx] if idx < len(documents) else ""
            if not accession or not document:
                continue
            candidates.append((
                0 if report_date == end else 1,
                0 if filing_date == filed else 1,
                0 if form == expected_form else 1,
                filing_date,
                form,
                accession,
                document,
            ))
        if not candidates:
            continue
        _, _, _, filing_date, form, accession, document = min(candidates)
        matches.append({
            "period_end": end,
            "filed": filing_date,
            "form": form,
            "url": ARCHIVES_URL.format(
                cik=cik, accession=accession.replace("-", ""), document=document,
            ),
        })
    return matches

def open_sec_filing_pdf(url):
    """Print a trusted SEC filing to PDF, then open it with the system PDF handler."""
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname != "www.sec.gov" or not SEC_FILING_PATH.fullmatch(parsed.path):
        raise ValueError("Only direct SEC filing documents can be opened as PDFs.")
    if "@" not in SEC_USER_AGENT or re.search(r"your[-_ ]?email|example\.(?:com|org|net)", SEC_USER_AGENT, re.I):
        raise RuntimeError(
            "Set SEC_USER_AGENT in backend/.env to your app or company name and a real contact email, then restart the backend."
        )

    browser = _pdf_browser()
    if not browser:
        raise RuntimeError("Install Microsoft Edge or Google Chrome to create the filing PDF.")

    cache_dir = Path(tempfile.gettempdir()) / "financial-dashboard-filing-pdfs"
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_key = f"{SEC_USER_AGENT}\n{url}"
    pdf_path = cache_dir / f"{hashlib.sha256(cache_key.encode('utf-8')).hexdigest()}.pdf"
    if not pdf_path.exists() or pdf_path.stat().st_size < 5 or pdf_path.read_bytes()[:5] != b"%PDF-":
        with tempfile.TemporaryDirectory(prefix="financial-dashboard-pdf-profile-") as profile:
            command = [
                browser,
                "--headless",
                "--disable-gpu",
                "--no-first-run",
                "--no-default-browser-check",
                f"--user-agent={SEC_USER_AGENT}",
                f"--user-data-dir={profile}",
                f"--print-to-pdf={pdf_path}",
                url,
            ]
            try:
                subprocess.run(
                    command, check=False, timeout=120,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
            except (OSError, subprocess.TimeoutExpired) as exc:
                raise RuntimeError("The filing could not be converted to PDF.") from exc
    if not pdf_path.exists() or pdf_path.stat().st_size < 5 or pdf_path.read_bytes()[:5] != b"%PDF-":
        raise RuntimeError("The filing could not be converted to PDF.")

    try:
        if os.name == "nt":
            os.startfile(str(pdf_path))
        else:
            opener = "open" if sys.platform == "darwin" else "xdg-open"
            if not shutil.which(opener):
                raise RuntimeError("No system PDF application opener is available.")
            subprocess.Popen(
                [opener, str(pdf_path)],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
    except OSError as exc:
        raise RuntimeError("No default PDF application could open the filing.") from exc


def _pdf_browser():
    for name in ("msedge", "msedge.exe", "google-chrome", "google-chrome-stable", "chrome", "chrome.exe", "chromium", "chromium-browser"):
        found = shutil.which(name)
        if found:
            return found
    if os.name == "nt":
        program_dirs = (os.environ.get("ProgramFiles(x86)"), os.environ.get("ProgramFiles"))
        for directory in program_dirs:
            if not directory:
                continue
            for relative in (
                "Microsoft/Edge/Application/msedge.exe",
                "Google/Chrome/Application/chrome.exe",
            ):
                executable = Path(directory) / relative
                if executable.is_file():
                    return str(executable)
    return None
