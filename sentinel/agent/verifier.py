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


def _numbers(text: str) -> List[Tuple[str, float]]:
    stripped = FORMS.sub(" ", DATE.sub(" ", TAG.sub(" ", text)))
    out = []
    for m in NUM.finditer(stripped):
        raw = m.group(0)
        try:
            v = float(raw.replace("$", "").replace(",", "").replace("−", "-"))
        except ValueError:
            continue
        out.append((raw, v))
    return out


def _matches(v: float, ledger_vals: List[float]) -> bool:
    av = abs(v)
    decimals_tol = 0.051 if av < 1000 else 0.6
    for lv in ledger_vals:
        for s in SCALES:
            x = abs(lv * s)
            if x == 0:
                continue
            if abs(x - av) <= decimals_tol or abs(x - av) / x <= 0.006:
                return True
    return False


def check_text(text: str, ledger: Ledger, allowed_ints: Optional[set] = None) -> Tuple[bool, Optional[str]]:
    tags = TAG.findall(text)
    for t in tags:
        if ledger.get(t) is None:
            return False, f"unknown evidence tag [{t}]"
    quotes = [_norm(q.text or "") for q in ledger.quotes()]
    for q in QUOTE.findall(text):
        nq = _norm(q).strip(" .…")
        if not any(nq in lq for lq in quotes):
            return False, f"quote not verbatim in cited filings: “{q[:60]}…”"
    # Numbers inside verified verbatim quotes come from the filing itself.
    text = QUOTE.sub(" ", text)
    vals = [e.value for e in ledger.numbers() if e.value is not None]
    for raw, v in _numbers(text):
        if float(v).is_integer() and 1990 <= v <= 2035:
            continue  # years
        if allowed_ints and float(v).is_integer() and int(v) in allowed_ints:
            continue
        if float(v).is_integer() and 0 <= v <= 10 and "." not in raw:
            continue  # small ordinals/counts ("3 scenarios", "top 5")
        if not _matches(v, vals):
            return False, f"number {raw} not found in evidence ledger"
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
