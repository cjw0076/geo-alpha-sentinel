"""Audit ledger: records chain by hash; any edit or deletion is detected."""

import json
from pathlib import Path

from sentinel import audit
from sentinel.models import AnalysisResult

SNAP = Path(__file__).resolve().parent.parent / "public" / "snapshots"


def _result(ticker: str) -> AnalysisResult:
    raw = json.loads((SNAP / ticker / "analysis.json").read_text(encoding="utf-8"))
    raw.pop("audit", None)
    return AnalysisResult(**raw)


def test_chain_appends_and_verifies(tmp_path):
    ledger = tmp_path / "ledger.jsonl"
    for t in ("NVDA", "TSM", "GOOGL"):
        audit.append(ledger, _result(t), code="test")
    recs = audit.read_ledger(ledger)
    assert len(recs) == 3
    assert recs[0]["prev_hash"] == audit.GENESIS
    assert recs[1]["prev_hash"] == recs[0]["hash"]
    assert audit.verify_chain(recs) is None


def test_tampering_is_detected(tmp_path):
    ledger = tmp_path / "ledger.jsonl"
    for t in ("NVDA", "TSM", "GOOGL"):
        audit.append(ledger, _result(t), code="test")
    recs = audit.read_ledger(ledger)
    recs[1]["outputs"]["recommendation"] = "ACCUMULATE"
    assert audit.verify_chain(recs) == 1
    recs = audit.read_ledger(ledger)
    del recs[0]
    assert audit.verify_chain(recs) == 0


def test_record_is_deterministic():
    r = _result("NVDA")
    a = audit.run_record(r, code="x")
    b = audit.run_record(r, code="x")
    assert a["hash"] == b["hash"]
    assert set(a["outputs"]["metrics"]) == {"CPI", "GPI", "SRS", "FAR"}
