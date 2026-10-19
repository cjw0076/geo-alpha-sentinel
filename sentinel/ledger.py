"""Evidence ledger: the single place where numbers and quotes enter an analysis.

The verifier later checks every figure and quote in the generated memo against
this ledger; anything not found here is rejected.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from sentinel.http import utcnow_iso
from sentinel.models import Evidence, Provenance

METHODOLOGY_URL = "https://github.com/cjw0076/geo-alpha-sentinel/blob/main/docs/METHODOLOGY.md"


class Ledger:
    def __init__(self) -> None:
        self.items: List[Evidence] = []
        self._by_key: Dict[str, str] = {}

    def add(
        self,
        kind: str,
        label: str,
        provenance: Provenance,
        value: Optional[float] = None,
        unit: Optional[str] = None,
        text: Optional[str] = None,
        data: Any = None,
        key: Optional[str] = None,
    ) -> str:
        if key and key in self._by_key:
            return self._by_key[key]
        eid = f"E{len(self.items) + 1}"
        self.items.append(
            Evidence(id=eid, kind=kind, label=label, value=None if value is None else float(value), unit=unit, text=text, data=data, provenance=provenance)
        )
        if key:
            self._by_key[key] = eid
        return eid

    def model_value(self, label: str, value: float, unit: str, locator: str, data: Any = None, key: Optional[str] = None) -> str:
        prov = Provenance(
            source="model",
            title=f"Geo-Alpha Sentinel methodology — {locator}",
            url=f"{METHODOLOGY_URL}#{locator.lower().replace(' ', '-')}",
            retrieved_at=utcnow_iso(),
            license="Apache-2.0 (computed from the cited evidence)",
            locator=locator,
        )
        return self.add("number", label, prov, value=value, unit=unit, data=data, key=key)

    def get(self, eid: str) -> Optional[Evidence]:
        for e in self.items:
            if e.id == eid:
                return e
        return None

    def numbers(self) -> List[Evidence]:
        return [e for e in self.items if e.value is not None]

    def quotes(self) -> List[Evidence]:
        return [e for e in self.items if e.kind == "quote" and e.text]
