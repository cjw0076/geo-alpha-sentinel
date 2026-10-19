"""Claim verifier (the Red-Team's deterministic half).

A claim passes only if
  1. every evidence tag [E#] it cites exists in the ledger,
  2. every number in its text equals a ledger number (after unit scaling) or a
     year / count that appears in the cited evidence,
  3. every quoted span ("...") appears verbatim in a ledger quote.
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple

from sentinel.ledger import Ledger
from sentinel.models import Claim

TAG = re.compile(r"\[(E\d+)\]")
NUM = re.compile(r"(?<![A-Za-z0-9])[-−]?\$?\d{1,3}(?:,\d{3})+(?:\.\d+)?|(?<![A-Za-z0-9])[-−]?\$?\d+(?:\.\d+)?")
QUOTE = re.compile(r"[\"“]([^\"”]{25,600})[\"”]")
SCALES = (1.0, 1e-9, 1e-6, 1e-3, 100.0, 0.01)


def _norm(s: str) -> str:
    s = re.sub(r"[’‘“”\"]", "'", s).replace("—", "-").replace("–", "-")
    return re.sub(r"\s+", " ", s).strip().lower()


DATE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b|\b\d{1,2}(?:-|/)\d{1,2}(?:-|/)\d{2,4}\b")
FORMS = re.compile(r"\b(10-K|10-Q|20-F|40-F|8-K|Item\s+\d+[A-Z]?(?:\.\d+)?|HS\s?\d{4,6}|FY\d{2,4}|Q[1-4])\b", re.I)


NUM_UNIT = re.compile(
    r"(?<![A-Za-z0-9.])([-−]?)\$?(\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)\s*(B|bn|billion|M|mn|million|K|%|조|억)?(?![A-Za-z0-9])"
)
UNIT_SCALE = {"B": 1e9, "bn": 1e9, "billion": 1e9, "M": 1e6, "mn": 1e6, "million": 1e6, "K": 1e3, "조": 1e12, "억": 1e8}
TAG_GROUP = re.compile(r"(?:\[E\d+\])+")


def _numbers(text: str) -> List[Tuple[str, float, Optional[str]]]:
    """(raw, value, unit) for every number outside dates/form names/tags."""
    stripped = FORMS.sub(" ", DATE.sub(" ", TAG.sub(" ", text)))
    out = []
    for m in NUM_UNIT.finditer(stripped):
        try:
            v = float(m.group(2).replace(",", ""))
        except ValueError:
            continue
        if m.group(1):
            v = -v
        out.append((m.group(0).strip(), v, m.group(3)))
    return out


def _trivial(raw: str, v: float, unit: Optional[str]) -> bool:
    if unit in (None, "%") and float(v).is_integer() and 1990 <= v <= 2035 and unit is None:
        return True  # year
    if unit is None and float(v).is_integer() and 0 <= v <= 10 and "." not in raw:
        return True  # small ordinal / count
    return False


def _value_matches(v: float, unit: Optional[str], ev_value: float, ev_unit: Optional[str]) -> bool:
    if unit in UNIT_SCALE:
        target = v * UNIT_SCALE[unit]
        tol = max(0.0051 * abs(ev_value), 0.051 * UNIT_SCALE[unit])
        return abs(abs(ev_value) - abs(target)) <= tol
    candidates = [ev_value]
    if unit == "%" or ev_unit in ("0-1",):
        candidates.append(ev_value * 100.0)
    if ev_unit == "USD":
        candidates += [ev_value / 1e9, ev_value / 1e6]
    for c in candidates:
        if abs(abs(c) - abs(v)) <= 0.051 or (c and abs(abs(c) - abs(v)) / abs(c) <= 0.006):
            return True
    return False


def _segments(text: str) -> List[Tuple[str, List[str]]]:
    """Split text into (segment, tags that close it). Trailing text inherits the previous tags."""
    segs, pos, last_tags = [], 0, []
    for m in TAG_GROUP.finditer(text):
        tags = TAG.findall(m.group(0))
        segs.append((text[pos : m.start()], tags))
        pos, last_tags = m.end(), tags
    if pos < len(text):
        segs.append((text[pos:], last_tags))
    return segs


def check_text(text: str, ledger: Ledger, allowed_ints: Optional[set] = None) -> Tuple[bool, Optional[str]]:
    for t in TAG.findall(text):
        if ledger.get(t) is None:
            return False, f"unknown evidence tag [{t}]"
    quotes = [_norm(q.text or "") for q in ledger.quotes()]
    for q in QUOTE.findall(text):
        nq = _norm(q).strip(" .…")
        if not any(nq in lq for lq in quotes):
            return False, f"quote not verbatim in cited filings: “{q[:60]}…”"
    text = QUOTE.sub(" ", text)  # numbers inside verified verbatim quotes come from the filing itself
    for seg, tags in _segments(text):
        evs = [ledger.get(t) for t in tags]
        evs = [e for e in evs if e is not None and e.value is not None]
        for raw, v, unit in _numbers(seg):
            if _trivial(raw, v, unit) or (allowed_ints and float(v).is_integer() and int(v) in allowed_ints):
                continue
            if not evs:
                return False, f"number {raw} has no evidence tag next to it"
            if not any(_value_matches(v, unit, e.value, e.unit) for e in evs):
                return False, f"number {raw} does not match its cited evidence {', '.join('[' + e.id + ']=' + format(e.value, 'g') for e in evs)}"
    return True, None


def verify_claims(claims: List[Dict], ledger: Ledger) -> List[Claim]:
    out = []
    for c in claims:
        text = str(c.get("text", "")).strip()
        ids = [i for i in c.get("evidence_ids", []) if isinstance(i, str)]
        ids = list(dict.fromkeys(ids + TAG.findall(text)))
        if not text:
            continue
        if not ids:
            out.append(Claim(text=text, evidence_ids=[], verified=False, reason="no evidence cited"))
            continue
        missing = [i for i in ids if ledger.get(i) is None]
        if missing:
            out.append(Claim(text=text, evidence_ids=ids, verified=False, reason=f"unknown evidence {', '.join(missing)}"))
            continue
        ok, reason = check_text(text, ledger)
        out.append(Claim(text=text, evidence_ids=ids, verified=ok, reason=reason))
    return out


def verify_sections(sections: List[Dict[str, str]], ledger: Ledger) -> List[Dict[str, str]]:
    """Return problems found in memo body text: [{'heading', 'reason'}]."""
    problems = []
    for s in sections:
        ok, reason = check_text(s.get("body", ""), ledger)
        if not ok:
            problems.append({"heading": s.get("heading", ""), "reason": reason or ""})
    return problems
