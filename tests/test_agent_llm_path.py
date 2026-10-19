"""Exercise the Claude code path with a scripted fake gateway: plan → triples →
memo with one hallucinated number → verifier rejects → replan → clean memo."""

import json

import pytest

from sentinel.agent import llm
from sentinel.agent import runner as R
from sentinel.models import AnalysisResult, Scenario


@pytest.mark.network
def test_llm_path_replans_on_hallucinated_number(monkeypatch):
    calls = {"memo": 0}

    def fake_chat_json(messages, model=None, max_tokens=0, timeout=0):
        sys_txt = messages[0]["content"]
        user = messages[1]["content"]
        if "Orchestrator" in sys_txt:
            out = {"hypotheses": ["Taiwan foundry dependence dominates downside."], "focus": ["taiwan"], "extra_entities": [], "extra_minerals": ["gallium"],
                   "step_notes": {"portwatch.chokepoints": "Taiwan Strait transits matter most."}}
        elif "Graph agent" in sys_txt:
            sents = json.loads(user)["sentences"]
            out = {"triples": [{"subject": "NVIDIA", "relation": "RESTRICTED_BY", "object": sents[0]["text"].split()[0], "category": "weaponized", "region": "China", "severity": 4, "sid": sents[0]["sid"]}]}
        elif "Synthesizer" in sys_txt:
            calls["memo"] += 1
            facts = json.loads(user)["FACTS"]
            rev = next(f for f in facts if f["label"].startswith("Revenue FY"))
            metric = json.loads(user)["METRICS"]
            rec = json.loads(user)["policy"]["computed_by_policy"]
            bad = "매출은 $999.9B[%s]입니다." % rev["id"] if calls["memo"] == 1 else "매출은 %s[%s]입니다." % (rev["value"], rev["id"])
            out = {"title": "t", "recommendation": rec, "summary": bad,
                   "sections": [{"heading": "요약", "body": bad}],
                   "claims": [{"text": bad, "evidence_ids": [rev["id"]]}, {"text": "CPI %s[%s]" % (metric["CPI"]["value"], metric["CPI"]["evidence_id"]), "evidence_ids": [metric["CPI"]["evidence_id"]]}],
                   "hedges": [{"theme": "x", "rationale": "y"}]}
        else:  # red team
            out = {"counterpoints": []}
        return {"json": out, "model": "fake", "usage": {}, "ms": 1}

    monkeypatch.setattr(llm, "enabled", lambda: True)
    monkeypatch.setattr(llm, "take_run_budget", lambda: True)
    monkeypatch.setattr(llm, "chat_json", fake_chat_json)
    items = list(R.run_analysis("NVDA", Scenario()))
    result = items[-1]
    assert isinstance(result, AnalysisResult)
    types = [e.type for e in result.events]
    assert "replan" in types, "verifier should reject $999.9B and trigger a replan"
    assert calls["memo"] == 2
    assert result.engine["replans"] == 1
    assert all(c.verified for c in result.memo.claims)
    assert "999.9" not in json.dumps(result.memo.model_dump(), ensure_ascii=False)
