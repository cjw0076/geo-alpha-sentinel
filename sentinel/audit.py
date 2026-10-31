"""Hash-chained audit ledger for agent runs.

Each record fixes what a run saw (filing, data as-of per source, scenario), what
produced it (code version, methodology versions, LLM id) and what it concluded
(recommendation, metric values, memo and evidence digests). Every record carries
the hash of the previous one, so editing or deleting any past record breaks the
chain from that point on (`verify_chain`).
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from sentinel.models import AnalysisResult

GENESIS = "0" * 64
METHODOLOGY_VERSIONS = {"metrics": "metrics-1.0", "stress": "stress-1.0", "verifier": "verifier-2.0"}


def canonical(obj: Any) -> bytes:
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def sha256(obj: Any) -> str:
    return hashlib.sha256(obj if isinstance(obj, bytes) else canonical(obj)).hexdigest()


def code_version() -> str:
    sha = os.getenv("GITHUB_SHA")
    if sha:
        return sha
    try:
        out = subprocess.run(["git", "rev-parse", "HEAD"], cwd=Path(__file__).resolve().parent.parent,
                             capture_output=True, text=True, timeout=5)
        return out.stdout.strip() or "unknown"
    except Exception:
        return "unknown"


def _as_of_by_source(result: AnalysisResult) -> Dict[str, str]:
    latest: Dict[str, str] = {}
    for e in result.evidence:
        src, ts = e.provenance.source, e.provenance.retrieved_at or ""
        if ts > latest.get(src, ""):
            latest[src] = ts
    return dict(sorted(latest.items()))


def run_record(result: AnalysisResult, prev_hash: str = GENESIS, code: Optional[str] = None) -> Dict[str, Any]:
    filing = result.company.latest_annual
    body = {
        "run_id": result.run_id,
        "ticker": result.ticker,
        "generated_at": result.generated_at,
        "scenario": result.scenario.model_dump(),
        "inputs": {
            "filing": {"form": filing.form, "accession": filing.accession, "filed": filing.filed} if filing else None,
            "as_of": _as_of_by_source(result),
            "evidence_n": len(result.evidence),
        },
        "producer": {
            "code_version": code or code_version(),
            "methodology": METHODOLOGY_VERSIONS,
            "llm": result.engine.get("llm"),
            "replans": result.engine.get("replans"),
        },
        "outputs": {
            "recommendation": result.memo.recommendation,
            "metrics": {m.code: (m.rating if m.code == "FAR" else m.value) for m in result.metrics},
            "verified_claims": result.engine.get("verified_claims"),
            "rejected_claims": result.engine.get("rejected_claims"),
            "memo_sha256": sha256(result.memo.model_dump()),
            "evidence_sha256": sha256([e.model_dump() for e in result.evidence]),
        },
        "prev_hash": prev_hash,
    }
    return {**body, "hash": sha256(body)}


def verify_chain(records: Iterable[Dict[str, Any]]) -> Optional[int]:
    """Return None if the chain is intact, else the index of the first bad record."""
    prev = GENESIS
    for i, rec in enumerate(records):
        body = {k: v for k, v in rec.items() if k != "hash"}
        if rec.get("prev_hash") != prev or sha256(body) != rec.get("hash"):
            return i
        prev = rec["hash"]
    return None


def read_ledger(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def append(path: Path, result: AnalysisResult, code: Optional[str] = None) -> Dict[str, Any]:
    records = read_ledger(path)
    prev = records[-1]["hash"] if records else GENESIS
    rec = run_record(result, prev, code)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False, separators=(",", ":")) + "\n")
    return rec
