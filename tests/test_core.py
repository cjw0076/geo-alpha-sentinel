"""Offline tests for the deterministic core: sandbox, stress, verifier, metrics, exposure."""

import json
from pathlib import Path

import pytest

from sentinel import sandbox
from sentinel.agent import verifier
from sentinel.analytics import metrics as M
from sentinel.analytics.exposure import analyze_exposure
from sentinel.analytics.stress import run_stress
from sentinel.ledger import Ledger
from sentinel.models import AnalysisResult, FilingRef, Provenance, Scenario
from sentinel.sources.filings import RiskSection, split_sentences, text_fragment_url

MOCK = Path(__file__).resolve().parent.parent / "public" / "mock"


def nvda() -> AnalysisResult:
    events = json.loads((MOCK / "analyze_NVDA.events.json").read_text())
    return AnalysisResult(**[e for e in events if e["event"] == "result"][0]["data"])


# ---- sandbox ---------------------------------------------------------------

@pytest.mark.parametrize("code", [
    "import os\nresult = 1",
    "result = __import__('os')",
    "result = open('/etc/passwd').read()",
    "result = (1).__class__.__bases__",
    "result = np.load('x')",
    "result = eval('1+1')",
    "class A: pass\nresult = 1",
])
def test_sandbox_rejects_escapes(code):
    with pytest.raises(sandbox.SandboxError):
        sandbox.run(code, {})


def test_sandbox_timeout_kills_isolated_child():
    with pytest.raises(sandbox.SandboxError):
        sandbox.run("x = 0\nwhile True:\n    x += 1\nresult = x", {}, timeout_s=0.5, isolate=True)


def test_sandbox_isolated_matches_inprocess():
    code = "result = float(np.random.default_rng(seed).normal(size=1000).mean())"
    assert sandbox.run(code, {"seed": 7}, isolate=True)["result"] == sandbox.run(code, {"seed": 7})["result"]


def test_sandbox_numpy_and_inputs():
    out = sandbox.run("result = {'m': float(np.mean(xs)), 'p': np.percentile(xs, 50)}", {"xs": [1, 2, 3, 4]})
    assert out["result"] == {"m": 2.5, "p": 2.5}


# ---- stress ------------------------------------------------------------------

def test_stress_is_deterministic_and_monotone():
    chans = {"taiwan": 0.8, "china_export": 0.6, "hormuz": 0.3, "malacca": 0.4}
    kw = dict(base_revenue=100e9, gross_margin=0.6, base_fcf=20e9, base_year=2025, paths=5000)
    a = run_stress(Scenario(taiwan=0.5), chans, **kw)
    b = run_stress(Scenario(taiwan=0.5), chans, **kw)
    c = run_stress(Scenario(taiwan=1.0), chans, **kw)
    assert a.revenue_shock_pct == b.revenue_shock_pct
    assert c.revenue_shock_pct["mean"] < a.revenue_shock_pct["mean"]
    assert 0 <= a.prob_fcf_drawdown_gt_20 <= c.prob_fcf_drawdown_gt_20 <= 1
    assert sum(a.histogram["counts"]) == 5000


# ---- verifier ------------------------------------------------------------------

def _ledger():
    led = Ledger()
    prov = Provenance(source="sec_edgar", title="t", url="https://www.sec.gov/x", retrieved_at="2026-01-01T00:00:00Z", license="PD")
    e1 = led.add("number", "Revenue", prov, value=215.94e9, unit="USD")
    e2 = led.add("number", "Gross margin", prov, value=71.07, unit="%")
    e3 = led.add("quote", "q", prov, text="Data centers depend on access to clean water and predictable energy.")
    return led, e1, e2, e3


def test_verifier_accepts_ledger_numbers_and_quotes():
    led, e1, e2, e3 = _ledger()
    ok, why = verifier.check_text(f"FY2026 매출 $215.9B[{e1}], 마진 71.1%[{e2}]. “Data centers depend on access to clean water”[{e3}]", led)
    assert ok, why


@pytest.mark.parametrize("text,reason", [
    ("매출 $230.0B[E1]", "number"),
    ("“Data centers are immune to energy shocks of any kind at all”[E3]", "quote"),
    ("근거 [E99]", "unknown"),
])
def test_verifier_rejects_ungrounded(text, reason):
    led, *_ = _ledger()
    ok, why = verifier.check_text(text, led)
    assert not ok and reason in why


def test_claims_without_evidence_are_rejected():
    led, *_ = _ledger()
    claims = verifier.verify_claims([{"text": "Revenue grew strongly.", "evidence_ids": []}], led)
    assert claims[0].verified is False


# ---- exposure ------------------------------------------------------------------

def test_exposure_tags_and_links():
    f = FilingRef(form="10-K", filed="2026-02-25", accession="0001-26-000001", primary_document="x.htm", url="https://www.sec.gov/Archives/x.htm")
    text = ("We depend on TSMC in Taiwan for substantially all of our wafers. Export controls imposed by the U.S. government restrict sales to China. "
            "Droughts in Taiwan could limit water available to our foundry partners. Rising electricity prices and power grid constraints may affect data centers.")
    sec = RiskSection(filing=f, title="Item 1A. Risk Factors", text=text, sentences=split_sentences(text), full_text=text)
    e = analyze_exposure("TEST", sec)
    assert e.categories["weaponized"]["hits"] >= 1 and e.categories["water"]["hits"] >= 1 and e.categories["watts"]["hits"] >= 1
    assert {g.region for g in e.geographies} >= {"Taiwan", "China"}
    assert "TSMC" in e.entities_named
    url = e.categories["weaponized"]["top"][0]["url"]
    assert url.startswith(f.url + "#:~:text=")


def test_text_fragment_url_encodes():
    u = text_fragment_url("https://a/b.htm", "Export controls, sanctions & tariffs could harm our business in the People's Republic of China.")
    assert "#:~:text=" in u and "," in u.split("#:~:text=")[1] and "&" not in u.split("#:~:text=")[1]


# ---- metrics on a real captured result --------------------------------------------

def test_metrics_bounds_and_policy():
    r = nvda()
    mets = M.compute_metrics(r.company, r.exposure, [(m, "sector") for m in r.minerals], r.chokepoints, r.sanctions, r.stress, Ledger(), {})
    by = {m.code: m for m in mets}
    for m in mets:
        assert 0 <= m.value <= 100
        assert abs(sum(c.weight for c in m.components) - 1.0) < 1e-9
    assert by["FAR"].rating in {"AAA", "AA", "A", "BBB", "BB", "B", "CCC"}


def test_captured_memo_is_fully_verified():
    r = nvda()
    led = Ledger()
    led.items = r.evidence
    for s in r.memo.sections:
        ok, why = verifier.check_text(s["body"], led)
        assert ok, (s["heading"], why)
    assert all(c.verified for c in r.memo.claims)
