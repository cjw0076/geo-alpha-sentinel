"""Trade.gov Consolidated Screening List (CSL) sanction screening source.

Fast in-memory entity screening using token normalization, inverted token indexing,
and SequenceMatcher scoring with subset-containment boosting.
"""

from __future__ import annotations

import difflib
import gzip
import json
import re
from functools import lru_cache
from typing import Any, Dict, List, Optional, Set, Tuple

from sentinel.config import DATA_DIR
from sentinel.http import utcnow_iso
from sentinel.models import Provenance, SanctionsMatch, SanctionsScreen

CSL_GZ_PATH = DATA_DIR / "csl_entities.json.gz"
CSL_URL = "https://data.trade.gov/downloadable_consolidated_screening_list/v1/consolidated.csv"

CORPORATE_SUFFIXES: Set[str] = {
    "co",
    "ltd",
    "inc",
    "corp",
    "corporation",
    "company",
    "limited",
    "llc",
    "group",
    "holdings",
    "technologies",
    "technology",
    "the",
}


def normalize_tokens(text: str) -> Tuple[Set[str], str]:
    """Normalize text: casefold, strip punctuation, remove corporate suffixes.

    Returns (set of tokens, sorted token string).
    """
    cleaned = re.sub(r"[^\w\s]", " ", text.lower())
    words = [w for w in cleaned.split() if w not in CORPORATE_SUFFIXES and len(w) > 1]
    if not words:
        words = [w for w in cleaned.split() if len(w) > 0]
    return set(words), " ".join(sorted(words))


class _SanctionsIndex:
    def __init__(self, entities: List[Dict[str, Any]], snapshot_date: str, count: int) -> None:
        self.entities = entities
        self.snapshot_date = snapshot_date
        self.count = count
        self.inverted_index: Dict[str, Set[int]] = {}

        # Pre-process entity token sets and normalized strings
        self.entity_norms: List[Dict[str, Any]] = []
        for idx, e in enumerate(entities):
            name = e.get("n", "")
            alts = e.get("a", [])

            tok_set, norm_str = normalize_tokens(name)
            alt_norms = [normalize_tokens(a) for a in alts]

            self.entity_norms.append({
                "tok_set": tok_set,
                "norm_str": norm_str,
                "alt_norms": alt_norms,
            })

            for t in tok_set:
                self.inverted_index.setdefault(t, set()).add(idx)
            for a_tok_set, _ in alt_norms:
                for t in a_tok_set:
                    self.inverted_index.setdefault(t, set()).add(idx)


@lru_cache(maxsize=1)
def _get_index() -> _SanctionsIndex:
    if not CSL_GZ_PATH.exists():
        raise FileNotFoundError(
            f"CSL entities snapshot not found at {CSL_GZ_PATH}. Run etl/build_sanctions.py first."
        )
    with gzip.open(CSL_GZ_PATH, "rt", encoding="utf-8") as f:
        data = json.load(f)

    entities = data.get("entities", [])
    snapshot_date = str(data.get("snapshot_date", "2026-10-19"))
    count = int(data.get("count", len(entities)))
    return _SanctionsIndex(entities, snapshot_date, count)


def screen(query: str, limit: int = 10) -> SanctionsScreen:
    """Screen an organization name against the Consolidated Screening List.

    Returns matches with score >= 0.86 sorted descending.
    """
    idx = _get_index()
    q_tokens, q_norm = normalize_tokens(query)

    # Fast candidate filtering using token inverted index
    candidate_indices: Set[int] = set()
    for t in q_tokens:
        if t in idx.inverted_index:
            candidate_indices.update(idx.inverted_index[t])

    raw_matches: List[SanctionsMatch] = []

    for entity_idx in candidate_indices:
        e = idx.entities[entity_idx]
        e_norm_meta = idx.entity_norms[entity_idx]

        best_score = 0.0
        best_matched_name = e.get("n", "")

        # Candidate name list: primary name + alt names
        candidates: List[Tuple[str, Set[str], str]] = [
            (e.get("n", ""), e_norm_meta["tok_set"], e_norm_meta["norm_str"])
        ]
        alts = e.get("a", [])
        for alt_str, (alt_tok_set, alt_norm_str) in zip(alts, e_norm_meta["alt_norms"]):
            candidates.append((alt_str, alt_tok_set, alt_norm_str))

        for cand_name, cand_tok_set, cand_norm in candidates:
            # Token string sequence similarity
            score = difflib.SequenceMatcher(None, q_norm, cand_norm).ratio()

            # Exact token subset containment boost
            if q_tokens and q_tokens.issubset(cand_tok_set):
                containment_boost = 0.88 + 0.12 * (len(q_tokens) / max(len(cand_tok_set), 1))
                score = max(score, containment_boost)

            if score > best_score:
                best_score = score
                best_matched_name = cand_name

        if best_score >= 0.86:
            raw_matches.append(
                SanctionsMatch(
                    name=e.get("n", ""),
                    matched_name=best_matched_name,
                    score=round(best_score, 3),
                    list_source=e.get("s", ""),
                    programs=e.get("p"),
                    country=e.get("c"),
                    entity_number=e.get("e"),
                    source_list_url=e.get("u"),
                )
            )

    raw_matches.sort(key=lambda m: m.score, reverse=True)
    matches = raw_matches[:limit]

    provenance = Provenance(
        source="trade_csl",
        title="US Trade.gov Consolidated Screening List",
        url=CSL_URL,
        retrieved_at=utcnow_iso(),
        license="Public domain (U.S. Government work)",
        locator=f"Query: {query}",
    )

    return SanctionsScreen(
        query=query,
        matches=matches,
        list_size=idx.count,
        snapshot_date=idx.snapshot_date,
        provenance=provenance,
    )


def screen_many(names: List[str], limit_per_query: int = 10) -> List[SanctionsScreen]:
    """Screen multiple organization names against CSL."""
    return [screen(name, limit=limit_per_query) for name in names]
