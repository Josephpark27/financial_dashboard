import requests
import pandas as pd
from io import StringIO

from app.sec import HEADERS

BASE = "https://www.sec.gov/Archives/edgar/data/1513845/"
CIK_JSON = "https://data.sec.gov/submissions/CIK0001513845.json"

filings = requests.get(CIK_JSON, headers=HEADERS, timeout=30).json()["filings"]["recent"]
accessions = [
    (filings["filingDate"][i], filings["accessionNumber"][i].replace("-", ""))
    for i, form in enumerate(filings["form"])
    if form == "6-K"
][:20]

for filing_date, accession in accessions:
    directory_url = f"{BASE}{accession}/index.json"
    items = requests.get(directory_url, headers=HEADERS, timeout=30).json()["directory"]["item"]
    exhibits = [
        item["name"] for item in items
        if "ex99-1" in item["name"].lower() and item["name"].lower().endswith(".htm")
    ]
    for exhibit in exhibits:
        url = f"{BASE}{accession}/{exhibit}"
        try:
            tables = pd.read_html(StringIO(requests.get(url, headers=HEADERS, timeout=30).text))
        except Exception as exc:
            print(f"{filing_date} {accession} {url}: could not parse ({exc})")
            continue
        for index, table in enumerate(tables):
            text = table.astype(str).to_string().lower()
            if "revenue" in text:
                labels = [str(value) for value in table.iloc[:15, 0].tolist()]
                print(f"{filing_date} {accession} {url} table {index}: {labels}")
                break
        else:
            print(f"{filing_date} {accession} {url}: no revenue table")