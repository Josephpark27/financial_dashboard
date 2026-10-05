from datetime import date, datetime, timedelta


def candidates(facts, kind):
    taxonomies = facts.get("facts", {})
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

    namespaces = ["us-gaap"] + [name for name in taxonomies if name != "us-gaap"]
    return [
        (namespace, concept)
        for concept in preferred
        for namespace in namespaces
        if concept in taxonomies.get(namespace, {})
    ]


def parse_date(s):
    return datetime.fromisoformat(s).date()


def _candidate_score(rows, cutoff, internal_cutoff):
    recent_quarters = set()
    recent_cumulative = set()
    historical_quarters = set()
    latest_end = date.min
    for row in rows:
        if "start" not in row or "end" not in row:
            continue
        try:
            start, end = parse_date(row["start"]), parse_date(row["end"])
        except (TypeError, ValueError):
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
    return len(recent_quarters), len(recent_cumulative), len(historical_quarters), latest_end


def _clean_periods(raw_rows):
    """Keep the latest value for a period and its original filing metadata."""
    by_period = {}
    for row in raw_rows:
        if "start" not in row or "end" not in row:
            continue
        try:
            start, end = parse_date(row["start"]), parse_date(row["end"])
            value = float(row["val"])
        except (TypeError, ValueError, KeyError):
            continue
        days = (end - start).days + 1
        filed = row.get("filed")
        clean = {
            "start": start, "end": end, "value": value, "filed": filed,
            "fy": row.get("fy"), "fp": row.get("fp"), "days": days,
            "latest_filed": filed or "",
        }
        key = (start, end)
        previous = by_period.get(key)
        if previous is None:
            by_period[key] = clean
            continue

        # Later SEC filings can repeat older periods as comparative data. Keep
        # their latest value, but retain the original filing date and fiscal
        # metadata for the period shown in the dashboard.
        if filed and (not previous["filed"] or filed < previous["filed"]):
            previous["filed"] = filed
            previous["fy"] = row.get("fy")
            previous["fp"] = row.get("fp")
        if (filed or "") >= previous["latest_filed"]:
            previous["value"] = value
            previous["latest_filed"] = filed or ""

    for row in by_period.values():
        row.pop("latest_filed", None)
    return list(by_period.values())


def _quarters_for_concept(raw_rows, concept_name):
    clean = _clean_periods(raw_rows)
    direct = [dict(row, source="reported quarterly", concept=concept_name)
              for row in clean if 70 <= row["days"] <= 120]
    cumulative = [row for row in clean if 140 <= row["days"] <= 380]
    derived = []
    for row in cumulative:
        priors = [
            previous for previous in cumulative
            if previous["start"] == row["start"]
            and previous["end"] < row["end"]
            and previous["days"] < row["days"]
        ]
        if not priors:
            continue
        previous = max(priors, key=lambda item: item["end"])
        quarter_days = (row["end"] - previous["end"]).days
        if 70 <= quarter_days <= 120:
            derived.append({
                "start": previous["end"] + timedelta(days=1), "end": row["end"],
                "value": row["value"] - previous["value"], "filed": row.get("filed"),
                "fy": row.get("fy"), "fp": "Q4" if row.get("fp") == "FY" else row.get("fp"),
                "days": quarter_days, "source": "derived from YTD", "concept": concept_name,
            })

    # When SEC facts contain both a direct quarterly value and one derived
    # from cumulative totals, prefer the directly reported quarterly value.
    combined = direct + derived
    combined.sort(key=lambda item: (
        item["end"], 0 if item["source"] == "reported quarterly" else 1,
        item.get("filed") or "",
    ))
    by_end = {}
    for row in combined:
        by_end.setdefault(row["end"], row)
    return list(by_end.values())


def quarter_metric(facts, years_back, kind):
    taxonomies = facts.get("facts", {})
    candidate_names = candidates(facts, kind)
    if not candidate_names:
        raise ValueError(f"No standard SEC {kind} concept was found for this company.")

    cutoff = date.today() - timedelta(days=365 * years_back)
    internal_cutoff = cutoff - timedelta(days=500)
    ranked = []
    for position, (namespace, concept) in enumerate(candidate_names):
        rows = taxonomies[namespace][concept].get("units", {}).get("USD", [])
        score = _candidate_score(rows, cutoff, internal_cutoff)
        display_name = concept if namespace == "us-gaap" else f"{namespace}:{concept}"
        ranked.append((score, position, display_name, rows))
    ranked.sort(key=lambda item: (item[0], -item[1]), reverse=True)

    # A single SEC concept can have a longer recent history but omit older
    # periods that another valid concept reports. Use the strongest series as
    # the primary source, then fill only its missing quarter ends from the
    # remaining standard concepts.
    quarter_by_end = {}
    used_concepts = []
    for _, _, display_name, rows in ranked:
        for quarter in _quarters_for_concept(rows, display_name):
            existing = quarter_by_end.get(quarter["end"])
            if existing is None:
                quarter_by_end[quarter["end"]] = quarter
                if display_name not in used_concepts:
                    used_concepts.append(display_name)
            elif quarter.get("filed") and (
                not existing.get("filed") or quarter["filed"] < existing["filed"]
            ):
                # Another valid SEC tag may carry this period's original
                # filing, while the primary tag only repeats it as a later
                # comparative. Preserve the primary value but use the earliest
                # filing's fiscal labels and report date.
                existing["filed"] = quarter["filed"]
                existing["fy"] = quarter.get("fy")
                existing["fp"] = quarter.get("fp")

    if not quarter_by_end:
        raise ValueError(f"Could not identify quarterly {kind} facts in the SEC data.")

    quarters = [quarter_by_end[end] for end in sorted(quarter_by_end)]
    quarters = [quarter for quarter in quarters if quarter["end"] >= internal_cutoff]
    for index, row in enumerate(quarters):
        prior = quarters[index - 4]["value"] if index >= 4 else None
        row["yoy_pct"] = ((row["value"] / prior) - 1) * 100 if prior not in (None, 0) else None
        fiscal_period = row.get("fp")
        fiscal_year = row.get("fy")
        if fiscal_period == "FY":
            fiscal_period = "Q4"
        if not fiscal_period:
            fiscal_period = f"Q{(row['end'].month - 1) // 3 + 1}"
        if fiscal_year is None:
            fiscal_year = row["end"].year
        row["fiscal_period"] = f"{int(fiscal_year)} {fiscal_period}"
        row["period_end"] = row["end"].isoformat()
        row["start_date"] = row["start"].isoformat()

    quarters = [quarter for quarter in quarters if quarter["end"] >= cutoff]
    display_concept = used_concepts[0] if used_concepts else "SEC facts"
    if len(used_concepts) > 1:
        display_concept += " (+ supplemental SEC concepts)"
    for row in quarters:
        row["concept"] = display_concept
    return display_concept, quarters
