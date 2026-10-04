from datetime import date, datetime, timedelta


def candidates(facts, kind):
    us_gaap = facts.get("facts", {}).get("us-gaap", {})
    if kind == "revenue":
        preferred = [
            "RevenueFromContractWithCustomerExcludingAssessedTax",
            "SalesRevenueNet", "Revenues", "SalesRevenueGoodsNet",
            "RevenueFromContractWithCustomerIncludingAssessedTax",
        ]
    else:
        preferred = [
            "NetIncomeLoss", "ProfitLoss", "NetIncomeLossAttributableToParent",
            "NetIncomeLossAvailableToCommonStockholdersBasic",
        ]
    return [c for c in preferred if c in us_gaap]

def parse_date(s):
    return datetime.fromisoformat(s).date()

def quarter_metric(facts, years_back, kind):
    us_gaap = facts.get("facts", {}).get("us-gaap", {})
    candidate_names = candidates(facts, kind)
    if not candidate_names:
        raise ValueError(f"No standard SEC {kind} concept was found for this company.")

    cutoff = date.today() - timedelta(days=365 * years_back)
    internal_cutoff = cutoff - timedelta(days=500)
    best = None
    best_score = None

    for concept in candidate_names:
        rows = us_gaap[concept].get("units", {}).get("USD", [])
        recent_quarters = set()
        recent_cumulative = set()
        historical_quarters = set()
        latest_end = date.min
        for row in rows:
            if "start" not in row or "end" not in row:
                continue
            try:
                start, end = parse_date(row["start"]), parse_date(row["end"])
            except Exception:
                continue
            days = (end - start).days + 1
            if 70 <= days <= 120:
                historical_quarters.add(end)
                if end >= cutoff:
                    recent_quarters.add(end)
            elif 140 <= days <= 380 and end >= cutoff:
                recent_cumulative.add(end)
            if 70 <= days <= 380 and end >= internal_cutoff:
                latest_end = max(latest_end, end)
        score = (len(recent_quarters), len(recent_cumulative), len(historical_quarters), latest_end)
        if best_score is None or score > best_score:
            best_score, best = score, (concept, rows)

    concept, raw_rows = best
    clean = []
    for row in raw_rows:
        if "start" not in row or "end" not in row:
            continue
        try:
            start, end = parse_date(row["start"]), parse_date(row["end"])
            value = float(row["val"])
        except Exception:
            continue
        days = (end - start).days + 1
        filed = row.get("filed")
        clean.append({"start": start, "end": end, "value": value, "filed": filed,
                      "fy": row.get("fy"), "fp": row.get("fp"), "days": days})

    # Latest filing wins for an identical reporting period.
    by_period = {}
    for row in sorted(clean, key=lambda r: (r["start"], r["end"], r.get("filed") or "")):
        by_period[(row["start"], row["end"])] = row
    clean = list(by_period.values())

    direct = [dict(r, source="reported quarterly") for r in clean if 70 <= r["days"] <= 120]
    cumulative = [r for r in clean if 140 <= r["days"] <= 380]
    derived = []
    for row in cumulative:
        priors = [p for p in cumulative if p["start"] == row["start"] and p["end"] < row["end"] and p["days"] < row["days"]]
        if not priors:
            continue
        prev = max(priors, key=lambda r: r["end"])
        quarter_days = (row["end"] - prev["end"]).days
        if 70 <= quarter_days <= 120:
            derived.append({
                "start": prev["end"] + timedelta(days=1), "end": row["end"],
                "value": row["value"] - prev["value"], "filed": row.get("filed"),
                "fy": row.get("fy"), "fp": "Q4" if row.get("fp") == "FY" else row.get("fp"),
                "days": quarter_days, "source": "derived from YTD",
            })

    combined = direct + derived
    if not combined:
        raise ValueError(f"Could not identify quarterly {kind} facts in the SEC data.")

    # Prefer directly reported observations when both exist for the same period.
    combined.sort(key=lambda r: (r["end"], 1 if r["source"] == "derived from YTD" else 0, r.get("filed") or ""))
    by_end = {}
    for row in combined:
        by_end[row["end"]] = row
    quarters = [by_end[k] for k in sorted(by_end)]

    # Keep extra history for the first YoY comparison, then trim.
    quarters = [q for q in quarters if q["end"] >= internal_cutoff]
    for idx, row in enumerate(quarters):
        prior = quarters[idx - 4]["value"] if idx >= 4 else None
        row["yoy_pct"] = ((row["value"] / prior) - 1) * 100 if prior not in (None, 0) else None
        fp = row.get("fp")
        fy = row.get("fy")
        if fp == "FY":
            fp = "Q4"
        row["fiscal_period"] = f"{int(fy)} {fp}" if fy is not None and fp else "—"
        row["period_end"] = row["end"].isoformat()
        row["start_date"] = row["start"].isoformat()
        row["concept"] = concept

    quarters = [q for q in quarters if q["end"] >= cutoff]
    return concept, quarters
