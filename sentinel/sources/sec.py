"""SEC EDGAR: ticker lookup, company submissions and XBRL company facts.

Public domain (U.S. Government work). Fair-access policy: descriptive User-Agent,
<= 10 requests/second. https://www.sec.gov/os/accessing-edgar-data
"""

from __future__ import annotations

from functools import lru_cache
from typing import Dict, List, Optional, Tuple

from sentinel.http import fetch_json
from sentinel.models import CompanyProfile, FactPoint, FilingRef, Provenance

SEC_LICENSE = "Public domain (U.S. Government work, SEC EDGAR)"
ANNUAL_FORMS = ("10-K", "20-F", "40-F", "10-K405")

# Common aliases people type that are not SEC tickers.
TICKER_ALIASES = {"TSMC": "TSM", "2330.TW": "TSM", "SAMSUNG": "SSNLF", "GOOG": "GOOGL", "BRK.B": "BRK-B"}

# XBRL concept fallbacks (first available wins, per fiscal year).
CONCEPTS: Dict[str, List[Tuple[str, str]]] = {
    "revenue": [
        ("us-gaap", "Revenues"),
        ("us-gaap", "RevenueFromContractWithCustomerExcludingAssessedTax"),
        ("us-gaap", "SalesRevenueNet"),
        ("ifrs-full", "Revenue"),
    ],
    "gross_profit": [("us-gaap", "GrossProfit"), ("ifrs-full", "GrossProfit")],
    "operating_income": [("us-gaap", "OperatingIncomeLoss"), ("ifrs-full", "ProfitLossFromOperatingActivities")],
    "net_income": [("us-gaap", "NetIncomeLoss"), ("ifrs-full", "ProfitLoss")],
    "cfo": [
        ("us-gaap", "NetCashProvidedByUsedInOperatingActivities"),
        ("ifrs-full", "CashFlowsFromUsedInOperatingActivities"),
    ],
    "capex": [
        ("us-gaap", "PaymentsToAcquirePropertyPlantAndEquipment"),
        ("us-gaap", "PaymentsToAcquireProductiveAssets"),
        ("us-gaap", "PaymentsToAcquirePropertyPlantAndEquipmentAndIntangibleAssets"),
        ("ifrs-full", "PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities"),
    ],
    "ppe_net": [("us-gaap", "PropertyPlantAndEquipmentNet"), ("ifrs-full", "PropertyPlantAndEquipment")],
    "inventory": [("us-gaap", "InventoryNet"), ("ifrs-full", "Inventories")],
    "rd_expense": [
        ("us-gaap", "ResearchAndDevelopmentExpense"),
        ("us-gaap", "ResearchAndDevelopmentExpenseExcludingAcquiredInProcessCost"),
        ("ifrs-full", "ResearchAndDevelopmentExpense"),
    ],
    "cash": [("us-gaap", "CashAndCashEquivalentsAtCarryingValue"), ("ifrs-full", "CashAndCashEquivalents")],
    "total_assets": [("us-gaap", "Assets"), ("ifrs-full", "Assets")],
}


def _prov(title: str, url: str, retrieved: str, locator: Optional[str] = None) -> Provenance:
    return Provenance(source="sec_edgar", title=title, url=url, retrieved_at=retrieved, license=SEC_LICENSE, locator=locator)


@lru_cache(maxsize=1)
def _ticker_table() -> Tuple[Dict[str, Dict], str]:
    data, retrieved = fetch_json("https://www.sec.gov/files/company_tickers.json", ttl=7 * 86400)
    table = {}
    for row in data.values():
        table[row["ticker"].upper()] = {"cik": str(row["cik_str"]).zfill(10), "name": row["title"], "ticker": row["ticker"].upper()}
    return table, retrieved


def normalize_ticker(ticker: str) -> str:
    t = ticker.strip().upper()
    return TICKER_ALIASES.get(t, t)


def resolve(ticker: str) -> Optional[Dict]:
    table, _ = _ticker_table()
    t = normalize_ticker(ticker)
    return table.get(t) or table.get(t.replace(".", "-"))


def search(q: str, limit: int = 12) -> List[Dict]:
    q = q.strip().upper()
    if not q:
        return []
    table, _ = _ticker_table()
    exact, prefix, name_hits = [], [], []
    for t, row in table.items():
        if t == q:
            exact.append(row)
        elif t.startswith(q):
            prefix.append(row)
        elif len(q) >= 3 and q in row["name"].upper():
            name_hits.append(row)
    prefix.sort(key=lambda r: len(r["ticker"]))
    out = exact + prefix + name_hits
    return [{"ticker": r["ticker"], "cik": r["cik"], "name": r["name"]} for r in out[:limit]]


def submissions(cik: str) -> Tuple[Dict, str]:
    return fetch_json(f"https://data.sec.gov/submissions/CIK{cik}.json", ttl=6 * 3600)


def company_facts(cik: str) -> Tuple[Dict, str]:
    return fetch_json(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json", ttl=12 * 3600)


def filing_url(cik: str, accession: str, primary_document: str) -> str:
    return f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accession.replace('-', '')}/{primary_document}"


def _annual_from_block(block: Dict, cik: str) -> Optional[FilingRef]:
    forms = block.get("form", [])
    best = None
    for i, form in enumerate(forms):
        if form in ANNUAL_FORMS and (best is None or block["filingDate"][i] > block["filingDate"][best]):
            best = i
    if best is None:
        return None
    accn, doc = block["accessionNumber"][best], block["primaryDocument"][best]
    period = (block.get("reportDate") or [None] * len(forms))[best] or None
    return FilingRef(form=forms[best], filed=block["filingDate"][best], period=period, accession=accn, primary_document=doc, url=filing_url(cik, accn, doc))


def latest_annual_filing(sub: Dict, cik: str) -> Optional[FilingRef]:
    """Most recent 10-K/20-F/40-F; frequent filers push it out of `recent`, so page older files."""
    found = _annual_from_block(sub.get("filings", {}).get("recent", {}), cik)
    if found:
        return found
    for extra in sub.get("filings", {}).get("files", [])[:3]:
        block, _ = fetch_json(f"https://data.sec.gov/submissions/{extra['name']}", ttl=24 * 3600)
        found = _annual_from_block(block, cik)
        if found:
            return found
    return None


def _concept_points(facts: Dict, taxonomy: str, concept: str) -> Dict[int, FactPoint]:
    """One full-year value per fiscal year for a single XBRL concept (restatements win)."""
    from datetime import date

    node = facts.get("facts", {}).get(taxonomy, {}).get(concept)
    if not node:
        return {}
    units = node.get("units", {})
    unit_key = "USD" if "USD" in units else None
    if unit_key is None:
        return {}
    by_end: Dict[str, Dict] = {}
    for f in units[unit_key]:
        if f.get("form") not in ANNUAL_FORMS or f.get("fp") != "FY":
            continue
        start, end = f.get("start"), f.get("end")
        if start:  # duration fact: keep roughly one-year periods only
            try:
                days = (date.fromisoformat(end) - date.fromisoformat(start)).days
            except Exception:
                days = 365
            if days < 330 or days > 400:
                continue
        prev = by_end.get(end)
        if prev is None or f.get("filed", "") > prev.get("filed", ""):
            by_end[end] = f
    out: Dict[int, FactPoint] = {}
    for end, f in sorted(by_end.items()):
        out[int(end[:4])] = FactPoint(fy=int(end[:4]), fp="FY", end=end, value=float(f["val"]), form=f["form"], accn=f["accn"], filed=f["filed"])
    return out


def _annual_series(facts: Dict, concept_list: List[Tuple[str, str]], max_years: int = 6) -> List[FactPoint]:
    """Union of fiscal years across fallback concepts; earlier concepts in the list win a year."""
    merged: Dict[int, FactPoint] = {}
    for taxonomy, concept in reversed(concept_list):
        merged.update(_concept_points(facts, taxonomy, concept))
    if not merged:
        return []
    latest = max(merged)
    return [merged[k] for k in sorted(merged) if k > latest - max_years]


def _hq(sub: Dict) -> Optional[str]:
    biz = (sub.get("addresses") or {}).get("business") or {}
    if biz.get("isForeignLocation"):
        return (biz.get("country") or biz.get("stateOrCountryDescription") or "").upper() or None
    return biz.get("stateOrCountry") or None


def company_profile(ticker: str) -> CompanyProfile:
    row = resolve(ticker)
    if row is None:
        raise LookupError(f"{ticker}: not an SEC registrant ticker")
    cik = row["cik"]
    sub, sub_retrieved = submissions(cik)
    facts, facts_retrieved = company_facts(cik)
    fundamentals = {name: _annual_series(facts, concepts) for name, concepts in CONCEPTS.items()}
    fundamentals = {k: v for k, v in fundamentals.items() if v}
    currency = "USD"
    if not fundamentals.get("revenue") and facts.get("facts", {}).get("ifrs-full"):
        currency = "reporting currency (IFRS)"
    latest = latest_annual_filing(sub, cik)
    prov = [
        _prov(f"{sub.get('name')} — EDGAR submissions", f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={cik}", sub_retrieved, "submissions"),
        _prov(f"{sub.get('name')} — XBRL company facts", f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json", facts_retrieved, "companyfacts"),
    ]
    return CompanyProfile(
        ticker=row["ticker"],
        cik=cik,
        name=sub.get("name") or row["name"],
        sic=sub.get("sic"),
        sic_description=sub.get("sicDescription"),
        exchanges=[e for e in (sub.get("exchanges") or []) if e],
        state_of_incorporation=sub.get("stateOfIncorporation") or None,
        hq_country=_hq(sub),
        fiscal_year_end=sub.get("fiscalYearEnd"),
        filer_category=sub.get("category"),
        latest_annual=latest,
        fundamentals=fundamentals,
        currency=currency,
        provenance=prov,
    )
