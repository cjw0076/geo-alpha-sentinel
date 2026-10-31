"""Holdings books for the portfolio lens.

* `kic_13f()`   — Korea Investment Corp's latest Form 13F-HR information table (SEC EDGAR).
* `acwi_proxy()` — iShares MSCI ACWI ETF holdings from its latest Form N-PORT-P,
  equity-common rows only, Korea removed, renormalised to 1 (an MSCI ACWI ex Korea proxy).
* `map_identifiers()` — CUSIP/ISIN -> US ticker via the OpenFIGI v3 mapping API, persisted
  to `data/identifier_map.json` so reruns are offline; fallback: normalised issuer-name
  match against SEC `company_tickers.json`.

SEC requests go through `sentinel.http.fetch_bytes/fetch_json` with the SEC User-Agent and
a process-wide limit of 4 requests/second (`install_sec_rate_limit`).
"""

from __future__ import annotations

import json
import re
import threading
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, Iterable, List, Optional, Tuple

import httpx

from sentinel import http
from sentinel.config import DATA_DIR, SEC_USER_AGENT
from sentinel.http import fetch_bytes, fetch_json, utcnow_iso
from sentinel.models import HoldingRow, HoldingsBook, Provenance
from sentinel.sources import sec

KIC_CIK = "0001441689"
ISHARES_TRUST_CIK = "0001100663"
ACWI_SYMBOL = "ACWI"
ACWI_SERIES_NAME = "iShares MSCI ACWI ETF"
ACWI_SERIES_ID_FALLBACK = "S000021461"  # from https://www.sec.gov/files/company_tickers_mf.json (symbol ACWI)
BENCH_LABEL = "MSCI ACWI ex Korea proxy — iShares MSCI ACWI ETF holdings (SEC N-PORT), Korea removed"
SEC_FILING_LICENSE = "Public domain (SEC filing)"
OPENFIGI_URL = "https://api.openfigi.com/v3/mapping"
OPENFIGI_LICENSE = ("FIGI identifiers dedicated to the public domain by Bloomberg (OpenFIGI Terms of Service, 2018-11-27, §1); "
                    "data via OpenFIGI.com, provided as is; no endorsement by Bloomberg or OpenFIGI implied")
IDENTIFIER_MAP_PATH = DATA_DIR / "identifier_map.json"
EQUITY_CATS = ("EC",)          # N-PORT assetCat kept for the benchmark
REMOVED_COUNTRIES = ("KR",)    # N-PORT invCountry removed from the benchmark
DOLLAR_VALUES_FROM = "2023-01-03"  # Form 13F reports values in dollars (not thousands) from this filing date

# ---------------------------------------------------------------------------
# SEC access: <= 4 requests / second for this process
# ---------------------------------------------------------------------------

_sec_lock = threading.Lock()
_sec_state = {"last": 0.0, "interval": 0.25, "n": 0}


def _sec_request_hook(request: httpx.Request) -> None:
    if not request.url.host.endswith("sec.gov"):
        return
    with _sec_lock:
        wait = _sec_state["last"] + _sec_state["interval"] - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        _sec_state["last"] = time.monotonic()
        _sec_state["n"] += 1


def install_sec_rate_limit(rate_per_s: float = 4.0) -> None:
    """Space SEC requests made through `sentinel.http.client()` at >= 1/rate seconds (all threads)."""
    _sec_state["interval"] = 1.0 / rate_per_s
    c = http.client()
    hooks = c.event_hooks
    if _sec_request_hook not in hooks.get("request", []):
        hooks.setdefault("request", []).append(_sec_request_hook)
        c.event_hooks = hooks


def sec_requests_made() -> int:
    return int(_sec_state["n"])


def _sec_bytes(url: str, params: Optional[Dict[str, Any]] = None, ttl: int = 6 * 3600) -> Tuple[bytes, str]:
    install_sec_rate_limit()
    return fetch_bytes(url, params=params, ttl=ttl, headers={"User-Agent": SEC_USER_AGENT})


def _sec_json(url: str, ttl: int = 6 * 3600) -> Tuple[Any, str]:
    install_sec_rate_limit()
    return fetch_json(url, ttl=ttl, headers={"User-Agent": SEC_USER_AGENT})


def _archive_base(cik: str, accession: str) -> str:
    return f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accession.replace('-', '')}/"


def _index_url(cik: str, accession: str) -> str:
    return _archive_base(cik, accession) + f"{accession}-index.htm"


# ---------------------------------------------------------------------------
# XML helpers (namespace-agnostic)
# ---------------------------------------------------------------------------

def _xml(raw: bytes) -> ET.Element:
    root = ET.fromstring(raw)
    for el in root.iter():
        if isinstance(el.tag, str) and "}" in el.tag:
            el.tag = el.tag.rsplit("}", 1)[1]
    return root


def _txt(el: Optional[ET.Element], path: str) -> Optional[str]:
    if el is None:
        return None
    v = el.findtext(path)
    v = v.strip() if v is not None else None
    return v or None


def _num(v: Optional[str]) -> float:
    try:
        return float(str(v).replace(",", ""))
    except (TypeError, ValueError):
        return 0.0


def _mdy_to_iso(s: Optional[str]) -> Optional[str]:
    if not s:
        return None
    m = re.match(r"^(\d{2})-(\d{2})-(\d{4})$", s)
    return f"{m.group(3)}-{m.group(1)}-{m.group(2)}" if m else s


# ---------------------------------------------------------------------------
# Form 13F-HR
# ---------------------------------------------------------------------------

def parse_13f_infotable(raw: bytes) -> List[Dict[str, Any]]:
    """Every <infoTable> row: name, title_of_class, cusip, value (as reported), shares, shares_type, put_call."""
    root = _xml(raw)
    out = []
    for it in root.iter("infoTable"):
        out.append({
            "name": _txt(it, "nameOfIssuer") or "",
            "title_of_class": _txt(it, "titleOfClass"),
            "cusip": (_txt(it, "cusip") or "").upper(),
            "value": _num(_txt(it, "value")),
            "shares": _num(_txt(it, "shrsOrPrnAmt/sshPrnamt")),
            "shares_type": _txt(it, "shrsOrPrnAmt/sshPrnamtType") or "SH",
            "put_call": _txt(it, "putCall"),
        })
    return out


def parse_13f_cover(raw: bytes) -> Dict[str, Any]:
    root = _xml(raw)
    return {
        "filer": _txt(root, ".//filingManager/name"),
        "period": _mdy_to_iso(_txt(root, ".//periodOfReport") or _txt(root, ".//reportCalendarOrQuarter")),
        "submission_type": _txt(root, ".//submissionType"),
        "is_amendment": (_txt(root, ".//isAmendment") or "false").lower() == "true",
        "table_entry_total": int(_num(_txt(root, ".//tableEntryTotal"))),
        "table_value_total": _num(_txt(root, ".//tableValueTotal")),
    }


def value_multiplier(rows: List[Dict[str, Any]], table_value_total: Optional[float], filed: Optional[str] = None) -> Tuple[int, str]:
    """Return (multiplier to USD, explanation) for 13F `value` fields.

    1. `sum(value)` must agree with the cover page `tableValueTotal` (both in the same unit,
       ±0.5%), or be 1/1000 of it (info table in thousands, cover in dollars); otherwise raise.
    2. With matching units, the unit is read from the median implied price per share
       (value / shares on SH rows): < $1 means the values are in thousands. The filing-date
       rule (dollars from 2023-01-03) is the tie-breaker when no share rows exist.
    """
    total = sum(r["value"] for r in rows)
    if table_value_total:
        ratio = table_value_total / total if total else float("inf")
        if abs(ratio - 1.0) <= 0.005:
            pass
        elif abs(ratio / 1000.0 - 1.0) <= 0.005:
            return 1000, f"info-table sum {total:,.0f} = cover tableValueTotal {table_value_total:,.0f} / 1000 → values in thousands"
        else:
            raise ValueError(f"13F info-table sum {total:,.0f} disagrees with cover tableValueTotal {table_value_total:,.0f}")
    prices = sorted(r["value"] / r["shares"] for r in rows if (r.get("shares_type") or "SH") == "SH" and r.get("shares", 0) > 0 and not r.get("put_call"))
    by_date = 1 if (filed is None or filed >= DOLLAR_VALUES_FROM) else 1000
    if prices:
        med = prices[len(prices) // 2]
        mult = 1000 if med < 1.0 else 1
        why = f"median implied price/share {med:,.2f} in reported units → {'thousands' if mult == 1000 else 'dollars'}"
        if mult != by_date:
            why += f" (filing-date rule would give {'thousands' if by_date == 1000 else 'dollars'})"
    else:
        mult, why = by_date, f"filing-date rule ({'≥' if by_date == 1 else '<'} {DOLLAR_VALUES_FROM})"
    if table_value_total:
        why += f"; info-table sum {total:,.0f} matches cover tableValueTotal {table_value_total:,.0f}"
    return mult, why


def aggregate_13f(rows: List[Dict[str, Any]], multiplier: int = 1) -> Tuple[List[HoldingRow], List[HoldingRow]]:
    """Aggregate by CUSIP; option rows (putCall) are listed separately and carry no weight."""
    eq: Dict[str, Dict[str, Any]] = {}
    opt: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for r in rows:
        if r.get("put_call"):
            key2 = (r["cusip"], r["put_call"])
            d = opt.setdefault(key2, {**r, "value": 0.0, "shares": 0.0})
        else:
            d = eq.setdefault(r["cusip"], {**r, "value": 0.0, "shares": 0.0})
        d["value"] += r["value"] * multiplier
        d["shares"] += r.get("shares", 0.0)
    total = sum(d["value"] for d in eq.values())
    if total <= 0:
        raise ValueError("13F book has no non-option value")
    book = [HoldingRow(name=d["name"], cusip=d["cusip"], value_usd=d["value"], weight=d["value"] / total, title_of_class=d.get("title_of_class"),
                       shares=d["shares"]) for d in eq.values()]
    options = [HoldingRow(name=d["name"], cusip=d["cusip"], value_usd=d["value"], weight=0.0, title_of_class=d.get("title_of_class"), shares=d["shares"],
                          put_call=d["put_call"]) for d in opt.values()]
    book.sort(key=lambda r: -r.value_usd)
    return book, options


def _latest_13f(cik: str) -> Dict[str, str]:
    sub, _ = _sec_json(f"https://data.sec.gov/submissions/CIK{cik}.json")
    rc = sub["filings"]["recent"]
    best = None
    for i, form in enumerate(rc["form"]):
        if form == "13F-HR" and (best is None or rc["filingDate"][i] > rc["filingDate"][best]):
            best = i
    if best is None:
        raise LookupError(f"CIK {cik}: no 13F-HR in recent filings")
    return {"accession": rc["accessionNumber"][best], "filed": rc["filingDate"][best], "period": rc["reportDate"][best]}


def _filing_meta(cik: str, accession: str) -> Dict[str, Optional[str]]:
    sub, _ = _sec_json(f"https://data.sec.gov/submissions/CIK{cik}.json")
    rc = sub["filings"]["recent"]
    for i, a in enumerate(rc["accessionNumber"]):
        if a == accession:
            return {"accession": a, "filed": rc["filingDate"][i], "period": rc["reportDate"][i]}
    return {"accession": accession, "filed": None, "period": None}


def kic_13f(accession: Optional[str] = None, resolve: bool = True) -> HoldingsBook:
    """KIC (CIK 0001441689) Form 13F-HR → HoldingsBook (values in USD, weights over non-option rows)."""
    meta = _filing_meta(KIC_CIK, accession) if accession else _latest_13f(KIC_CIK)
    acc = meta["accession"]
    base = _archive_base(KIC_CIK, acc)
    idx, retrieved = _sec_json(base + "index.json", ttl=30 * 86400)
    xmls = [it["name"] for it in idx["directory"]["item"] if it["name"].lower().endswith(".xml")]
    info_name = next((n for n in xmls if n.lower() != "primary_doc.xml"), None)
    if info_name is None:
        raise LookupError(f"{acc}: information-table XML not found in filing index")
    info_raw, _ = _sec_bytes(base + info_name, ttl=30 * 86400)
    cover_raw, _ = _sec_bytes(base + "primary_doc.xml", ttl=30 * 86400)
    cover = parse_13f_cover(cover_raw)
    raw_rows = parse_13f_infotable(info_raw)
    if cover["table_entry_total"] and cover["table_entry_total"] != len(raw_rows):
        raise ValueError(f"{acc}: {len(raw_rows)} infoTable rows vs cover tableEntryTotal {cover['table_entry_total']}")
    mult, why = value_multiplier(raw_rows, cover["table_value_total"], meta.get("filed"))
    rows, options = aggregate_13f(raw_rows, mult)
    period = cover["period"] or meta.get("period") or ""
    filed = meta.get("filed")
    book = HoldingsBook(
        name="Korea Investment Corp — Form 13F-HR",
        as_of=period,
        filed=filed,
        source=Provenance(source="sec_edgar", title=f"{cover['filer'] or 'Korea Investment CORP'} Form 13F-HR, period {period}, filed {filed} (accession {acc})",
                          url=_index_url(KIC_CIK, acc), retrieved_at=retrieved, license=SEC_FILING_LICENSE, locator=f"information table {info_name}"),
        total_value_usd=sum(r.value_usd for r in rows),
        rows=rows,
        excluded=options,
        notes=[f"{len(raw_rows)} infoTable rows → {len(rows)} CUSIPs (aggregated), {len(options)} option rows excluded from weights",
               f"values ×{mult}: {why}"],
    )
    if resolve:
        resolve_book(book)
    return book


# ---------------------------------------------------------------------------
# Form N-PORT-P (iShares MSCI ACWI ETF)
# ---------------------------------------------------------------------------

def parse_nport(raw: bytes) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    """(genInfo + fundInfo summary, invstOrSec rows)."""
    root = _xml(raw)
    gen = root.find(".//genInfo")
    info = {
        "reg_name": _txt(gen, "regName"), "reg_cik": _txt(gen, "regCik"),
        "series_name": _txt(gen, "seriesName"), "series_id": _txt(gen, "seriesId"),
        "period": _txt(gen, "repPdDate") or _txt(gen, "repPdEnd"), "period_end": _txt(gen, "repPdEnd"),
        "net_assets": _num(_txt(root, ".//fundInfo/netAssets")),
    }
    rows = []
    for s in root.iter("invstOrSec"):
        ids = s.find("identifiers")
        isin = None
        if ids is not None:
            el = ids.find("isin")
            isin = (el.get("value") or "").strip().upper() if el is not None else None
        cond = s.find("assetConditional")
        cusip = (_txt(s, "cusip") or "").upper()
        rows.append({
            "name": _txt(s, "name") or "", "title": _txt(s, "title"),
            "cusip": cusip if re.fullmatch(r"[0-9A-Z]{9}", cusip or "") and cusip != "000000000" else None,
            "isin": isin or None,
            "value": _num(_txt(s, "valUSD")), "pct": _num(_txt(s, "pctVal")),
            "asset_cat": _txt(s, "assetCat") or (cond.get("assetCat") if cond is not None else None),
            "asset_desc": cond.get("desc") if cond is not None else None,
            "country": _txt(s, "invCountry"), "balance": _num(_txt(s, "balance")), "units": _txt(s, "units"),
        })
    return info, rows


def nport_equity_book(rows: List[Dict[str, Any]]) -> Tuple[List[HoldingRow], List[HoldingRow], Dict[str, float]]:
    """Keep EQUITY_CATS rows, drop REMOVED_COUNTRIES, aggregate by ISIN/CUSIP, renormalise to 1."""
    gross = sum(r["value"] for r in rows)
    eq = [r for r in rows if r["asset_cat"] in EQUITY_CATS and r["value"] > 0]
    removed = [r for r in eq if (r["country"] or "") in REMOVED_COUNTRIES]
    kept = [r for r in eq if (r["country"] or "") not in REMOVED_COUNTRIES]
    agg: Dict[str, Dict[str, Any]] = {}
    for r in kept:
        key = r["isin"] or r["cusip"] or r["name"]
        d = agg.setdefault(key, {**r, "value": 0.0, "balance": 0.0})
        d["value"] += r["value"]
        d["balance"] += r["balance"]
    total = sum(d["value"] for d in agg.values())
    if total <= 0:
        raise ValueError("N-PORT book has no equity value after filters")
    book = [HoldingRow(name=d["name"], cusip=d["cusip"], isin=d["isin"], value_usd=d["value"], weight=d["value"] / total, country=d["country"],
                       shares=d["balance"] if d.get("units") == "NS" else None, title_of_class=d.get("title")) for d in agg.values()]
    book.sort(key=lambda r: -r.value_usd)
    excl = [HoldingRow(name=r["name"], cusip=r["cusip"], isin=r["isin"], value_usd=r["value"], weight=0.0, country=r["country"]) for r in removed]
    eq_total = sum(r["value"] for r in eq)
    stats = {
        "gross_value_usd": gross, "equity_value_usd": eq_total, "kept_value_usd": total,
        "removed_value_usd": sum(r["value"] for r in removed), "removed_share_of_equity": (sum(r["value"] for r in removed) / eq_total) if eq_total else 0.0,
        "non_equity_rows": float(len(rows) - len(eq)), "non_equity_value_usd": gross - eq_total,
    }
    return book, excl, stats


def acwi_series_id() -> str:
    try:
        d, _ = _sec_json("https://www.sec.gov/files/company_tickers_mf.json", ttl=7 * 86400)
        for cik, series, _cls, sym in d["data"]:
            if sym == ACWI_SYMBOL and int(cik) == int(ISHARES_TRUST_CIK):
                return series
    except Exception:
        pass
    return ACWI_SERIES_ID_FALLBACK


def _series_nport_filings(series_id: str, count: int = 40) -> List[Dict[str, str]]:
    raw, _ = _sec_bytes("https://www.sec.gov/cgi-bin/browse-edgar",
                        params={"action": "getcompany", "CIK": series_id, "type": "NPORT-P", "dateb": "", "owner": "include", "count": str(count), "output": "atom"},
                        ttl=24 * 3600)
    root = _xml(raw)
    out = []
    for e in root.iter("entry"):
        c = e.find("content")
        if c is None or (_txt(c, "filing-type") or "") != "NPORT-P":
            continue
        out.append({"accession": _txt(c, "accession-number") or "", "filed": _txt(c, "filing-date") or ""})
    out.sort(key=lambda x: x["filed"], reverse=True)
    return out


def acwi_proxy(accession: Optional[str] = None, period: Optional[str] = None, resolve: bool = True) -> HoldingsBook:
    """Latest NPORT-P of the iShares MSCI ACWI ETF series (or the given accession / the latest
    report whose period end is on or before `period`), EC rows, Korea removed, renormalised."""
    series_id = acwi_series_id()
    if accession:
        cands = [{"accession": accession, "filed": None}]
    else:
        cands = _series_nport_filings(series_id)
        if not cands:
            raise LookupError(f"no NPORT-P filings found for series {series_id}")
    chosen = None
    for c in cands[:12]:
        base = _archive_base(ISHARES_TRUST_CIK, c["accession"])
        raw, retrieved = _sec_bytes(base + "primary_doc.xml", ttl=30 * 86400)
        info, rows = parse_nport(raw)
        if (info["series_name"] or "").strip().lower() != ACWI_SERIES_NAME.lower() or (info["series_id"] and info["series_id"] != series_id):
            raise ValueError(f"{c['accession']}: series is {info['series_name']!r} ({info['series_id']}), expected {ACWI_SERIES_NAME!r} ({series_id})")
        if period is None or (info["period_end"] or "") <= period:
            chosen = (c, info, rows, retrieved)
            break
    if chosen is None:
        raise LookupError(f"no ACWI NPORT-P with period end on or before {period}")
    c, info, rows, retrieved = chosen
    book_rows, excl, stats = nport_equity_book(rows)
    book = HoldingsBook(
        name=BENCH_LABEL,
        as_of=info["period_end"] or info["period"] or "",
        filed=c.get("filed"),
        source=Provenance(source="sec_edgar", title=f"{info['reg_name']} — {info['series_name']} ({info['series_id']}) Form NPORT-P, period {info['period_end']}"
                          + (f", filed {c['filed']}" if c.get("filed") else "") + f" (accession {c['accession']})",
                          url=_index_url(ISHARES_TRUST_CIK, c["accession"]), retrieved_at=retrieved, license=SEC_FILING_LICENSE, locator="Part C schedule of investments (invstOrSec)"),
        total_value_usd=sum(r.value_usd for r in book_rows),
        rows=book_rows,
        excluded=excl,
        notes=[f"{len(rows)} invstOrSec rows; kept assetCat in {list(EQUITY_CATS)}: {len(book_rows)} securities after removing invCountry in {list(REMOVED_COUNTRIES)}",
               f"removed Korea: {len(excl)} rows, {stats['removed_share_of_equity'] * 100:.2f}% of equity-common value",
               f"non-equity-common rows dropped: {int(stats['non_equity_rows'])} (value ${stats['non_equity_value_usd'] / 1e6:,.1f}M of ${stats['gross_value_usd'] / 1e6:,.1f}M gross)",
               f"fund net assets ${info['net_assets'] / 1e9:,.2f}B"],
    )
    if resolve:
        resolve_book(book)
    return book


# ---------------------------------------------------------------------------
# Identifier mapping: OpenFIGI v3 (+ SEC company-name fallback)
# ---------------------------------------------------------------------------

_LEGAL = {"INC", "INCORPORATED", "CORP", "CORPORATION", "CO", "COMPANY", "LTD", "LIMITED", "PLC", "AG", "SA", "NV", "AS", "ASA", "AB", "SE",
          "SPA", "OYJ", "KGAA", "LLC", "LP", "BHD", "BERHAD", "TBK", "PCL", "PJSC", "SAB", "CV", "DE", "THE", "BV", "SCA", "SAS", "NL"}


def normalise_name(name: Optional[str]) -> str:
    """Upper-case, '&'→AND, punctuation removed, split letters re-joined (N.V.→NV), legal-form suffixes and a leading THE removed."""
    if not name:
        return ""
    s = name.upper().replace("&", " AND ")   # parenthesised words are kept: 'Colgate-Palmolive (India)' is not Colgate-Palmolive
    toks = re.sub(r"[^A-Z0-9]+", " ", s).split()
    merged: List[str] = []
    run: List[str] = []
    for t in toks + [""]:
        if len(t) == 1 and t.isalpha():
            run.append(t)
            continue
        if run:
            merged.append("".join(run))
            run = []
        if t:
            merged.append(t)
    while merged and merged[-1] in _LEGAL:
        merged.pop()
    while merged and merged[0] == "THE":
        merged.pop(0)
    return " ".join(merged)


_name_idx_lock = threading.Lock()
_name_idx: Dict[str, Any] = {}


def _sec_name_index() -> Dict[str, List[Dict[str, str]]]:
    with _name_idx_lock:
        if "idx" not in _name_idx:
            install_sec_rate_limit()
            table, _ = sec._ticker_table()
            by_name: Dict[str, Dict[str, Dict[str, str]]] = {}
            for row in table.values():  # file order: one primary ticker per CIK (first seen)
                n = normalise_name(row["name"])
                if len(n) >= 4:
                    by_name.setdefault(n, {}).setdefault(row["cik"], row)
            _name_idx["idx"] = {k: list(v.values()) for k, v in by_name.items()}
        return _name_idx["idx"]


_LOOSE_DROP = {"HOLDINGS", "HOLDING", "GROUP"}


def loose_name(name: Optional[str]) -> str:
    """normalise_name without HOLDING(S)/GROUP tokens and without spaces ('EXXON MOBIL' == 'EXXONMOBIL HOLDINGS')."""
    return "".join(t for t in normalise_name(name).split() if t not in _LOOSE_DROP)


def _sec_loose_index() -> Dict[str, List[Dict[str, str]]]:
    with _name_idx_lock:
        if "loose" not in _name_idx:
            table, _ = sec._ticker_table()
            by: Dict[str, Dict[str, Dict[str, str]]] = {}
            for row in table.values():
                n = loose_name(row["name"])
                if len(n) >= 5:
                    by.setdefault(n, {}).setdefault(row["cik"], row)
            _name_idx["loose"] = {k: list(v.values()) for k, v in by.items()}
        return _name_idx["loose"]


def primary_ticker(cik: str) -> Optional[str]:
    """First ticker listed for a CIK in SEC company_tickers.json (e.g. TSM rather than TSMWF, GOOGL rather than GOOG)."""
    with _name_idx_lock:
        if "primary" not in _name_idx:
            table, _ = sec._ticker_table()
            prim: Dict[str, str] = {}
            for row in table.values():
                prim.setdefault(row["cik"], row["ticker"])
            _name_idx["primary"] = prim
        return _name_idx["primary"].get(cik)


def sec_name_match(*names: Optional[str]) -> Optional[Dict[str, str]]:
    """Unique CIK whose normalised SEC company name equals a normalised candidate name; second tier:
    the same comparison without HOLDING(S)/GROUP tokens and spaces."""
    idx = _sec_name_index()
    for n in names:
        hits = idx.get(normalise_name(n))
        if hits and len(hits) == 1:
            return hits[0]
    loose = _sec_loose_index()
    for n in names:
        ln = loose_name(n)
        hits = loose.get(ln) if len(ln) >= 5 else None
        if hits and len(hits) == 1:
            return hits[0]
    return None


def identifier_key(row: Any) -> Optional[str]:
    cusip = getattr(row, "cusip", None) if not isinstance(row, dict) else row.get("cusip")
    isin = getattr(row, "isin", None) if not isinstance(row, dict) else row.get("isin")
    if cusip and re.fullmatch(r"[0-9A-Z]{9}", cusip) and cusip != "000000000":
        return f"CUSIP:{cusip}"
    if isin and re.fullmatch(r"[A-Z]{2}[0-9A-Z]{9}[0-9]", isin):
        if isin[:2] in ("US", "CA"):
            return f"CUSIP:{isin[2:11]}"
        return f"ISIN:{isin}"
    return None


def load_identifier_map() -> Dict[str, Any]:
    try:
        return json.loads(IDENTIFIER_MAP_PATH.read_text())
    except Exception:
        return {"source": "OpenFIGI v3 mapping API (https://api.openfigi.com/v3/mapping)", "license": OPENFIGI_LICENSE, "items": {}}


def save_identifier_map(m: Dict[str, Any]) -> None:
    m["updated_at"] = utcnow_iso()
    m["source"] = "OpenFIGI v3 mapping API (https://api.openfigi.com/v3/mapping)"
    m["license"] = OPENFIGI_LICENSE
    m["items"] = dict(sorted(m["items"].items()))
    IDENTIFIER_MAP_PATH.parent.mkdir(parents=True, exist_ok=True)
    IDENTIFIER_MAP_PATH.write_text(json.dumps(m, ensure_ascii=False, separators=(",", ":"), sort_keys=False) + "\n")


FIGI_QUERY = "us2"  # jobs carry exchCode=US (the unfiltered result list is truncated and can miss the US composite)


def _figi_pick(data: List[Dict[str, Any]]) -> Dict[str, Optional[str]]:
    us = next((d for d in data if d.get("exchCode") == "US"), None)
    first = data[0] if data else {}
    return {
        "us_ticker": (us.get("ticker") or "").replace("/", "-") if us else None,
        "figi_name": (us or first).get("name"),
        "first_exch": first.get("exchCode"),
        "first_ticker": first.get("ticker"),
        "security_type": (us or first).get("securityType"),
    }


def _figi_job(key: str) -> Dict[str, str]:
    kind, val = key.split(":", 1)
    if kind == "ISIN":
        id_type = "ID_ISIN"
    else:
        id_type = "ID_CINS" if val[:1].isalpha() else "ID_CUSIP"   # CINS: foreign-issuer CUSIP form, first character a letter
    return {"idType": id_type, "idValue": val, "exchCode": "US"}


def _needs_query(item: Optional[Dict[str, Any]]) -> bool:
    return item is None or (not item.get("us_ticker") and item.get("q") != FIGI_QUERY)


def _openfigi_post(jobs: List[Dict[str, str]], client: httpx.Client) -> List[Dict[str, Any]]:
    for attempt in range(6):
        r = client.post(OPENFIGI_URL, json=jobs)
        if r.status_code == 429:
            reset = r.headers.get("ratelimit-reset")
            time.sleep(min(90.0, float(reset) + 1.0) if reset and reset.isdigit() else 30.0 * (attempt + 1))
            continue
        if r.status_code >= 500:
            time.sleep(5.0 * (attempt + 1))
            continue
        r.raise_for_status()
        return r.json()
    raise RuntimeError("OpenFIGI: repeated 429/5xx")


def openfigi_fetch(keys: Iterable[str], m: Dict[str, Any], log=print, min_interval_s: float = 2.5, batch: int = 10) -> int:
    """Map the given identifier keys that are not yet in `m['items']` (no API key: <=10 jobs/request,
    <=25 requests/minute). Persists after every request. Returns the number of requests made."""
    todo = [k for k in dict.fromkeys(keys) if k and _needs_query(m["items"].get(k))]
    if not todo:
        return 0
    n = 0
    with httpx.Client(timeout=httpx.Timeout(30.0, connect=10.0),
                      headers={"User-Agent": "GeoAlphaSentinel/2.0 (+https://github.com/cjw0076/geo-alpha-sentinel)", "Content-Type": "application/json"}) as client:
        last = 0.0
        for i in range(0, len(todo), batch):
            chunk = todo[i : i + batch]
            jobs = [_figi_job(k) for k in chunk]
            wait = last + min_interval_s - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            last = time.monotonic()
            res = _openfigi_post(jobs, client)
            n += 1
            now = utcnow_iso()
            for k, job in zip(chunk, res):
                prev = m["items"].get(k) or {}
                if "data" in job:
                    m["items"][k] = {**_figi_pick(job["data"]), "q": FIGI_QUERY, "retrieved_at": now}
                else:
                    m["items"][k] = {"us_ticker": None, "warning": job.get("warning") or job.get("error"), "q": FIGI_QUERY, "retrieved_at": now}
                if prev.get("figi_name") and not m["items"][k].get("figi_name"):
                    m["items"][k]["figi_name"] = prev["figi_name"]   # keep the name from the unfiltered query for the name fallback
            save_identifier_map(m)
            if n % 20 == 0:
                log(f"  openfigi {i + len(chunk)}/{len(todo)} identifiers")
    return n


def map_identifiers(rows: List[Any], log=print, fetch: bool = True) -> Dict[str, Dict[str, Any]]:
    """identifier key -> {ticker, cik, sec_name, map_method, figi...} for every row.

    Order: OpenFIGI US-composite ticker that resolves in SEC company_tickers.json ('openfigi');
    else a unique normalised-name match against SEC company_tickers.json ('sec_name'); else 'none'.
    """
    m = load_identifier_map()
    keys = [identifier_key(r) for r in rows]
    if fetch:
        openfigi_fetch([k for k in keys if k], m, log=log)
    out: Dict[str, Dict[str, Any]] = {}
    changed = False
    for r, k in zip(rows, keys):
        rk = k or f"NAME:{getattr(r, 'name', None) if not isinstance(r, dict) else r.get('name')}"
        if rk in out:
            continue
        item = m["items"].get(k, {}) if k else {}
        res: Dict[str, Any] = {"ticker": None, "cik": None, "map_method": "none", "figi_us_ticker": item.get("us_ticker"), "figi_name": item.get("figi_name")}
        t = item.get("us_ticker")
        hit = sec.resolve(t) if t else None
        if hit:
            res.update(ticker=primary_ticker(hit["cik"]) or hit["ticker"], cik=hit["cik"], sec_name=hit["name"], map_method="openfigi")
        else:
            name = getattr(r, "name", None) if not isinstance(r, dict) else r.get("name")
            title = getattr(r, "title_of_class", None) if not isinstance(r, dict) else r.get("title")
            hit = sec_name_match(name, item.get("figi_name"), title)
            if hit:
                res.update(ticker=primary_ticker(hit["cik"]) or hit["ticker"], cik=hit["cik"], sec_name=hit["name"], map_method="sec_name")
        if k and k in m["items"] and m["items"][k].get("resolved") != {kk: res.get(kk) for kk in ("ticker", "cik", "map_method")}:
            m["items"][k]["resolved"] = {kk: res.get(kk) for kk in ("ticker", "cik", "map_method")}
            changed = True
        out[rk] = res
    if changed:
        save_identifier_map(m)
    return out


# ---------------------------------------------------------------------------
# SEC filer status
# ---------------------------------------------------------------------------

_filer_cache: Dict[str, Optional[Dict[str, str]]] = {}
_filer_lock = threading.Lock()


def annual_filing(cik: str) -> Optional[Dict[str, str]]:
    """Latest 10-K/20-F/40-F of a CIK via `sec.latest_annual_filing` (cached per process)."""
    with _filer_lock:
        if cik in _filer_cache:
            return _filer_cache[cik]
    install_sec_rate_limit()
    try:
        sub, _ = sec.submissions(cik)
        f = sec.latest_annual_filing(sub, cik)
        val = {"form": f.form, "filed": f.filed, "url": f.url, "accession": f.accession} if f else None
    except Exception:
        val = None
    with _filer_lock:
        _filer_cache[cik] = val
    return val


MAX_FILING_AGE_DAYS = 550   # an annual report filed more than this many days before the book date does not count


def is_current_filer(filing: Optional[Dict[str, str]], as_of: Optional[str], max_age_days: int = MAX_FILING_AGE_DAYS) -> bool:
    from datetime import date

    if not filing:
        return False
    if not as_of or not filing.get("filed"):
        return True
    return (date.fromisoformat(as_of) - date.fromisoformat(filing["filed"])).days <= max_age_days


def resolve_book(book: HoldingsBook, log=print, max_workers: int = 4, fetch: bool = True) -> HoldingsBook:
    """Fill ticker / cik / map_method / sec_filer on every weighted row (in place). `sec_filer` means a
    10-K / 20-F / 40-F filed no more than MAX_FILING_AGE_DAYS before the book date (deregistered
    foreign issuers keep their last 20-F on EDGAR for years)."""
    mapping = map_identifiers(book.rows, log=log, fetch=fetch)
    for r in book.rows:
        k = identifier_key(r) or f"NAME:{r.name}"
        res = mapping.get(k) or {}
        r.ticker, r.cik, r.map_method = res.get("ticker"), res.get("cik"), res.get("map_method", "none")
    ciks = sorted({r.cik for r in book.rows if r.cik})
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        status = dict(zip(ciks, ex.map(annual_filing, ciks)))
    for r in book.rows:
        r.sec_filer = bool(r.cik and is_current_filer(status.get(r.cik), book.as_of))
    mapped = sum(r.weight for r in book.rows if r.ticker)
    filers = sum(r.weight for r in book.rows if r.sec_filer)
    book.notes.append(f"identifier mapping: {sum(1 for r in book.rows if r.ticker)}/{len(book.rows)} rows mapped to an SEC ticker "
                      f"({mapped * 100:.2f}% of weight; openfigi {sum(1 for r in book.rows if r.map_method == 'openfigi')}, sec_name {sum(1 for r in book.rows if r.map_method == 'sec_name')}); "
                      f"SEC annual-report filers {sum(1 for r in book.rows if r.sec_filer)} rows ({filers * 100:.2f}% of weight)")
    return book
