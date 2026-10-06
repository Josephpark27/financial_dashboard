from datetime import date, datetime, timedelta


def candidates(facts, kind):
    taxonomies = facts.get("facts", {})
    if kind == "revenue":
        preferred = [
            "RevenueFromContractWithCustomerExcludingAssessedTax",
            "SalesRevenueNet", "Revenues", "SalesRevenueGoodsNet",
            "RevenueFromContractWithCustomerIncludingAssessedTax",
        ]
    elif kind == "eps":
        preferred = [
            "EarningsPerShareDiluted", "EarningsPerShareBasic",
            "DilutedEarningsLossPerShare", "BasicEarningsLossPerShare",
        ]
    elif kind == "operating cash flow":
        preferred = [
            "NetCashProvidedByUsedInOperatingActivities",
            "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations",
            "CashFlowsFromUsedInOperatingActivities",
        ]
    elif kind == "capital expenditures":
        preferred = [
            "PaymentsToAcquirePropertyPlantAndEquipment",
            "PaymentsToAcquireProductiveAssets",
            "PaymentsToAcquirePropertyPlantAndEquipmentAndIntangibleAssets",
            "PaymentsToAcquireIntangibleAssets",
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


def add_growth_metrics(quarters):
    """Add sequential-quarter, YoY, and trailing-twelve-month series in place."""
    def end_date(row):
        value = row["end"]
        return value if isinstance(value, date) else parse_date(value)

    def adjacent_quarters(later, earlier):
        days = (end_date(later) - end_date(earlier)).days
        return 70 <= days <= 120

    def comparable_year(later, earlier):
        days = (end_date(later) - end_date(earlier)).days
        return 330 <= days <= 400

    for index, row in enumerate(quarters):
        previous_quarter = quarters[index - 1] if index >= 1 else None
        prior_year_quarter = quarters[index - 4] if index >= 4 else None
        ttm_chain = index >= 3 and all(
            adjacent_quarters(quarters[i], quarters[i - 1])
            for i in range(index - 2, index + 1)
        )

        row["qoq_pct"] = (
            ((row["value"] / previous_quarter["value"]) - 1) * 100
            if previous_quarter and adjacent_quarters(row, previous_quarter)
            and previous_quarter["value"] != 0 else None
        )
        row["yoy_pct"] = (
            ((row["value"] / prior_year_quarter["value"]) - 1) * 100
            if prior_year_quarter and comparable_year(row, prior_year_quarter)
            and all(adjacent_quarters(quarters[i], quarters[i - 1]) for i in range(index - 3, index + 1))
            and prior_year_quarter["value"] != 0 else None
        )

        row["ttm_value"] = (
            sum(quarters[i]["value"] for i in range(index - 3, index + 1))
            if ttm_chain else None
        )
        previous_ttm = quarters[index - 1].get("ttm_value") if index >= 1 else None
        prior_year_ttm = quarters[index - 4].get("ttm_value") if index >= 4 else None
        row["ttm_qoq_pct"] = (
            ((row["ttm_value"] / previous_ttm) - 1) * 100
            if row["ttm_value"] is not None and previous_ttm not in (None, 0)
            and previous_quarter and adjacent_quarters(row, previous_quarter) else None
        )
        row["ttm_yoy_pct"] = (
            ((row["ttm_value"] / prior_year_ttm) - 1) * 100
            if row["ttm_value"] is not None and prior_year_ttm not in (None, 0)
            and comparable_year(row, quarters[index - 4])
            and all(adjacent_quarters(quarters[i], quarters[i - 1]) for i in range(index - 3, index + 1))
            else None
        )


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
            # A first-quarter cash-flow fact often has the same fiscal-year
            # start as the six-month YTD fact, but is itself a direct
            # quarterly duration (70–120 days). Include direct facts here so
            # Q2 can be derived as six-month YTD less reported Q1.
            previous for previous in clean
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


def _eps_quarters_for_concept(raw_rows, concept_name):
    """Use reported quarterly EPS and derive Q4 from annual less Q1–Q3 EPS."""
    clean = _clean_periods(raw_rows)
    direct = [dict(row, source="reported quarterly", concept=concept_name)
              for row in clean if 70 <= row["days"] <= 120]
    annual = [row for row in clean if 350 <= row["days"] <= 380]
    derived = []

    for year_row in annual:
        fiscal_year = year_row.get("fy")
        quarters_by_fiscal_period = {}
        for row in direct:
            if row.get("fp") not in {"Q1", "Q2", "Q3"}:
                continue
            if row["start"] < year_row["start"] or row["end"] >= year_row["end"]:
                continue
            if fiscal_year is not None and row.get("fy") != fiscal_year:
                continue
            previous = quarters_by_fiscal_period.get(row["fp"])
            if previous is None or row["end"] > previous["end"]:
                quarters_by_fiscal_period[row["fp"]] = row

        first, second, third = (quarters_by_fiscal_period.get(period) for period in ("Q1", "Q2", "Q3"))
        if not (first and second and third):
            continue
        if not (70 <= (second["end"] - first["end"]).days <= 120
                and 70 <= (third["end"] - second["end"]).days <= 120
                and 70 <= (year_row["end"] - third["end"]).days <= 120):
            continue

        derived.append({
            "start": third["end"] + timedelta(days=1), "end": year_row["end"],
            "value": year_row["value"] - first["value"] - second["value"] - third["value"],
            "filed": year_row.get("filed"), "fy": fiscal_year, "fp": "Q4",
            "days": (year_row["end"] - third["end"]).days,
            "source": "derived from annual EPS", "concept": concept_name,
        })

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
        units = taxonomies[namespace][concept].get("units", {})
        if kind == "eps":
            rows = next((
                values for unit, values in units.items()
                if unit.replace(" ", "").lower() in {"usd/shares", "usd/share"}
            ), [])
        else:
            rows = units.get("USD", [])
        if not rows:
            continue
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
    # Basic and diluted EPS are different series, so never fill holes in one
    # with values from the other. Keep the highest-ranked EPS concept intact.
    selected_candidates = ranked[:1] if kind == "eps" else ranked
    for _, _, display_name, rows in selected_candidates:
        metric_quarters = (
            _eps_quarters_for_concept(rows, display_name)
            if kind == "eps" else _quarters_for_concept(rows, display_name)
        )
        for quarter in metric_quarters:
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

    # Calculate all derived series before applying the requested display
    # window. This keeps the first visible TTM values complete and lets YoY
    # comparisons reach back far enough for the full trailing year.
    add_growth_metrics(quarters)
    for row in quarters:
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


def free_cash_flow_metric(facts, years_back):
    """Derive free cash flow as operating cash flow less capital expenditures."""
    # Fetch two extra years so TTM and YoY growth remain complete at the start
    # of the requested display window.
    operating_concept, operating_rows = quarter_metric(
        facts, years_back + 2, "operating cash flow",
    )
    capex_concept, capex_rows = quarter_metric(
        facts, years_back + 2, "capital expenditures",
    )
    # The SEC reports both cash-flow components for the same fiscal quarter,
    # though concept tags can differ by a day in their reported start date.
    # Quarter end is the stable key after each component has been normalized
    # into quarterly values.
    capex_by_period = {row["end"]: row for row in capex_rows}
    free_cash_flow = []
    for operating in operating_rows:
        capex = capex_by_period.get(operating["end"])
        if capex is None:
            continue
        filing_dates = [value for value in (operating.get("filed"), capex.get("filed")) if value]
        free_cash_flow.append({
            "start": operating["start"],
            "end": operating["end"],
            "value": operating["value"] - abs(capex["value"]),
            "filed": min(filing_dates) if filing_dates else None,
            "fy": operating.get("fy") if operating.get("fy") is not None else capex.get("fy"),
            "fp": operating.get("fp") or capex.get("fp"),
            "source": "derived from operating cash flow and capital expenditures",
        })

    if not free_cash_flow:
        raise ValueError("Could not derive quarterly free cash flow from SEC cash flow facts.")

    free_cash_flow.sort(key=lambda row: row["end"])
    add_growth_metrics(free_cash_flow)
    for row in free_cash_flow:
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
        row["concept"] = f"{operating_concept} - {capex_concept}"

    cutoff = date.today() - timedelta(days=365 * years_back)
    free_cash_flow = [row for row in free_cash_flow if row["end"] >= cutoff]
    if not free_cash_flow:
        raise ValueError("Could not identify quarterly free cash flow in the SEC data.")
    return f"{operating_concept} - {capex_concept}", free_cash_flow


def _instant_quarters_for_concept(raw_rows, concept_name):
    """Collect balance-sheet facts, which are point-in-time rather than durations."""
    by_end = {}
    for row in raw_rows:
        if "end" not in row or "start" in row:
            continue
        try:
            end, value = parse_date(row["end"]), float(row["val"])
        except (TypeError, ValueError, KeyError):
            continue
        filed = row.get("filed")
        clean = {
            "end": end, "value": value, "filed": filed,
            "fy": row.get("fy"), "fp": row.get("fp"),
            "latest_filed": filed or "", "concept": concept_name,
        }
        previous = by_end.get(end)
        if previous is None:
            by_end[end] = clean
            continue
        if filed and (not previous["filed"] or filed < previous["filed"]):
            previous["filed"] = filed
            previous["fy"] = row.get("fy")
            previous["fp"] = row.get("fp")
        if (filed or "") >= previous["latest_filed"]:
            previous["value"] = value
            previous["latest_filed"] = filed or ""

    for row in by_end.values():
        row.pop("latest_filed", None)
    return list(by_end.values())


def _instant_metric_series(facts, preferred, cutoff):
    taxonomies = facts.get("facts", {})
    namespaces = ["us-gaap"] + [name for name in taxonomies if name != "us-gaap"]
    ranked = []
    for position, concept in enumerate(preferred):
        for namespace in namespaces:
            definition = taxonomies.get(namespace, {}).get(concept)
            if not definition:
                continue
            raw_rows = definition.get("units", {}).get("USD", [])
            display_name = concept if namespace == "us-gaap" else f"{namespace}:{concept}"
            rows = _instant_quarters_for_concept(raw_rows, display_name)
            recent = [row for row in rows if row["end"] >= cutoff]
            if not recent:
                continue
            score = (len(recent), max(row["end"] for row in recent))
            ranked.append((score, position, display_name, rows))

    ranked.sort(key=lambda item: (item[0], -item[1]), reverse=True)
    if not ranked:
        return None, {}

    by_end = {}
    used_concepts = []
    for _, _, display_name, rows in ranked:
        for row in rows:
            if row["end"] not in by_end:
                by_end[row["end"]] = row
                if display_name not in used_concepts:
                    used_concepts.append(display_name)

    concept_label = used_concepts[0]
    if len(used_concepts) > 1:
        concept_label += " (+ supplemental SEC concepts)"
    return concept_label, by_end


def balance_sheet_metric(facts, years_back):
    """Build quarterly assets/liabilities and derive equity as assets less liabilities."""
    cutoff = date.today() - timedelta(days=365 * years_back)
    assets_concept, assets_by_end = _instant_metric_series(facts, ["Assets"], cutoff)
    liabilities_concept, liabilities_by_end = _instant_metric_series(facts, ["Liabilities"], cutoff)
    if not assets_by_end or not liabilities_by_end:
        raise ValueError("Could not identify quarterly assets and liabilities in the SEC data.")

    periods = []
    for end in sorted(assets_by_end.keys() & liabilities_by_end.keys()):
        if end < cutoff:
            continue
        assets = assets_by_end[end]
        liabilities = liabilities_by_end[end]
        asset_value = assets["value"]
        liability_value = liabilities["value"]
        equity_value = asset_value - liability_value
        filing_dates = [value for value in (assets.get("filed"), liabilities.get("filed")) if value]
        fiscal_period = assets.get("fp") or liabilities.get("fp")
        fiscal_year = assets.get("fy") if assets.get("fy") is not None else liabilities.get("fy")
        if fiscal_period == "FY":
            fiscal_period = "Q4"
        if fiscal_period not in {"Q1", "Q2", "Q3", "Q4"}:
            fiscal_period = f"Q{(end.month - 1) // 3 + 1}"
        if fiscal_year is None:
            fiscal_year = end.year
        periods.append({
            "period_end": end.isoformat(),
            "fiscal_period": f"{int(fiscal_year)} {fiscal_period}",
            "filed": min(filing_dates) if filing_dates else None,
            "assets": asset_value,
            "liabilities": liability_value,
            "equity": equity_value,
        })

    if not periods:
        raise ValueError("Could not match quarterly assets and liabilities in the SEC data.")
    concept = f"{assets_concept} - {liabilities_concept}; equity = assets - liabilities"
    for row in periods:
        row["concept"] = concept
    return concept, periods
