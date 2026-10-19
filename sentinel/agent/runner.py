"""Multi-agent analysis run.

    Orchestrator ─► Graph agent (tools) ─► Simulation agent (sandbox) ─► metrics
         ▲                                                                 │
         └──── replan ◄── Red-Team (verifier + critique) ◄── Synthesizer ◄─┘

`run_analysis()` is a generator: it yields `AgentEvent`s while it works and
finally an `AnalysisResult`. With gateway credentials the Orchestrator,
Graph triage, Synthesizer and Red-Team critique use Claude; without them the
same pipeline runs with deterministic planners/templates. Every number shown
to the user is a ledger entry; the verifier rejects anything else.
"""

from __future__ import annotations

import json
import re
import time
import uuid
from typing import Any, Dict, Iterator, List, Optional, Tuple, Union

from sentinel import sandbox
from sentinel.agent import llm, verifier
from sentinel.analytics import metrics as M
from sentinel.analytics.exposure import analyze_exposure
from sentinel.analytics.stress import run_stress
from sentinel.config import MC_PATHS, MC_SEED
from sentinel.http import utcnow_iso
from sentinel.ledger import Ledger
from sentinel.models import (
    AgentEvent,
    AnalysisResult,
    Chokepoint,
    Claim,
    CompanyProfile,
    Exposure,
    Memo,
    Metric,
    Mineral,
    Provenance,
    SanctionsScreen,
    Scenario,
)
from sentinel.sources import filings, sec

SECTOR_HS = {
    "367": ["8542", "8486"],
    "357": ["8542", "8507"],
    "366": ["8542", "8505"],
    "371": ["8507", "8505"],
    "355": ["8486", "8505"],
    "362": ["8504", "7403"],
    "491": ["8504", "7403"],
}

CHANNEL_KO = {"taiwan": "대만해협·대만 공급", "china_export": "중국 핵심광물 수출통제", "hormuz": "호르무즈 에너지", "malacca": "말라카 해상로"}
CAT_KO = {"watts": "Watts(전력)", "wires": "Wires(구리·전력망)", "water": "Water(용수·기후)", "weaponized": "Weaponized(수출통제·자원무기화)"}


def fmt_usd(v: float) -> str:
    av = abs(v)
    if av >= 1e9:
        return f"${v / 1e9:,.1f}B"
    if av >= 1e6:
        return f"${v / 1e6:,.1f}M"
    return f"${v:,.0f}"


class Run:
    def __init__(self, ticker: str, scenario: Scenario) -> None:
        self.run_id = uuid.uuid4().hex[:12]
        self.ticker = ticker
        self.scenario = scenario
        self.ledger = Ledger()
        self.ev: Dict[str, str] = {}
        self.events: List[AgentEvent] = []
        self.t0 = time.perf_counter()
        self.use_llm = False
        self.llm_calls = 0
        self.llm_ms = 0

    def emit(self, agent: str, type_: str, title: str, detail: Optional[str] = None, data: Any = None) -> AgentEvent:
        e = AgentEvent(seq=len(self.events) + 1, t_ms=int((time.perf_counter() - self.t0) * 1000), agent=agent, type=type_, title=title, detail=detail, data=data)
        self.events.append(e)
        return e

    def ms(self) -> int:
        return int((time.perf_counter() - self.t0) * 1000)

    def call_llm_json(self, messages, model, max_tokens=2500) -> Optional[Dict]:
        if not self.use_llm:
            return None
        try:
            out = llm.chat_json(messages, model=model, max_tokens=max_tokens)
            self.llm_calls += 1
            self.llm_ms += out["ms"]
            return out["json"]
        except llm.LLMUnavailable as exc:
            self.emit("system", "thought", "LLM call failed — deterministic fallback", detail=str(exc)[:300])
            return None


# ---------------------------------------------------------------------------
# helpers that register evidence
# ---------------------------------------------------------------------------

def _xbrl_evidence(run: Run, p: CompanyProfile) -> None:
    labels = {
        "revenue": "Revenue", "gross_profit": "Gross profit", "operating_income": "Operating income", "net_income": "Net income",
        "cfo": "Cash from operations", "capex": "Capital expenditure", "cash": "Cash & equivalents", "ppe_net": "PP&E, net",
        "total_assets": "Total assets", "inventory": "Inventory", "rd_expense": "R&D expense",
    }
    for concept, label in labels.items():
        series = p.fundamentals.get(concept)
        if not series:
            continue
        pt = series[-1]
        url = f"https://www.sec.gov/Archives/edgar/data/{int(p.cik)}/{pt.accn.replace('-', '')}/"
        prov = Provenance(source="sec_edgar", title=f"{p.name} {pt.form} (filed {pt.filed}) — XBRL", url=url, retrieved_at=p.provenance[-1].retrieved_at if p.provenance else utcnow_iso(),
                          license=sec.SEC_LICENSE, locator=f"{concept} FY{pt.fy} (period end {pt.end})")
        run.ev[f"xbrl:{concept}"] = run.ledger.add("number", f"{label} FY{pt.fy}", prov, value=pt.value, unit="USD", data={"series": [x.model_dump() for x in series]}, key=f"xbrl:{concept}")


def _derived_numbers(run: Run, p: CompanyProfile) -> Tuple[Dict[str, float], AgentEvent]:
    """Margins, FCF, growth — computed in the sandbox from XBRL evidence."""
    inputs = {k: (p.fundamentals[k][-1].value if p.fundamentals.get(k) else None) for k in ("revenue", "gross_profit", "operating_income", "cfo", "capex", "cash")}
    rev_series = [x.value for x in p.fundamentals.get("revenue", [])]
    inputs["prev_revenue"] = rev_series[-2] if len(rev_series) >= 2 else None
    code = (
        "r = {}\n"
        "if revenue:\n"
        "    if gross_profit is not None: r['gross_margin_pct'] = gross_profit / revenue * 100\n"
        "    if operating_income is not None: r['operating_margin_pct'] = operating_income / revenue * 100\n"
        "    if cfo is not None and capex is not None:\n"
        "        r['fcf'] = cfo - capex\n"
        "        r['fcf_margin_pct'] = (cfo - capex) / revenue * 100\n"
        "    if prev_revenue: r['revenue_growth_pct'] = (revenue / prev_revenue - 1) * 100\n"
        "    if cash is not None: r['cash_to_revenue_pct'] = cash / revenue * 100\n"
        "result = r\n"
    )
    out = sandbox.run(code, inputs)
    res = out["result"]
    names = {
        "gross_margin_pct": ("Gross margin (latest FY)", "%"), "operating_margin_pct": ("Operating margin (latest FY)", "%"),
        "fcf": ("Free cash flow = CFO − capex (latest FY)", "USD"), "fcf_margin_pct": ("FCF margin (latest FY)", "%"),
        "revenue_growth_pct": ("Revenue growth YoY (latest FY)", "%"), "cash_to_revenue_pct": ("Cash / revenue", "%"),
    }
    prov = Provenance(source="sandbox", title="Deterministic sandbox — derived from SEC XBRL evidence", url="https://github.com/cjw0076/geo-alpha-sentinel/blob/main/sentinel/agent/runner.py",
                      retrieved_at=utcnow_iso(), license="Apache-2.0", locator="derived fundamentals")
    for k, v in res.items():
        if v is None:
            continue
        label, unit = names[k]
        run.ev[f"drv:{k}"] = run.ledger.add("number", label, prov, value=round(v, 4) if unit == "%" else v, unit=unit, data={"code": code}, key=f"drv:{k}")
    return res, run.emit("simulation", "tool_result", "sandbox.derived_fundamentals", detail=f"{len(res)} values in {out['ms']} ms", data={"tool": "sandbox.run", "summary": {k: round(v, 2) for k, v in res.items() if v is not None}, "evidence_ids": [run.ev[f'drv:{k}'] for k in res if f'drv:{k}' in run.ev], "ms": out["ms"]})


def _exposure_evidence(run: Run, exp: Exposure) -> None:
    f = exp.filing
    base_prov = dict(source="sec_edgar", url=f.url, retrieved_at=utcnow_iso(), license=sec.SEC_LICENSE)
    for cat, d in exp.categories.items():
        run.ev[f"exp:{cat}"] = run.ledger.model_value(
            f"{cat.capitalize()} exposure score (risk-factor sentence density)", d["score"], "score 0-100", "4W exposure score",
            data={"hits": d["hits"], "terms": d.get("terms", {}), "filing": f.url}, key=f"exp:{cat}")
        run.ev[f"exph:{cat}"] = run.ledger.add("number", f"{cat.capitalize()} risk sentences in {exp.section_title}", Provenance(title=f"{f.form} filed {f.filed} — {exp.section_title}", locator=exp.section_title, **base_prov),
                                               value=d["hits"], unit="sentences", key=f"exph:{cat}")
        for i, hit in enumerate(d.get("top", [])[:3]):
            qid = run.ledger.add("quote", f"{f.form} {exp.section_title} — {cat} ({hit['term']})",
                                 Provenance(title=f"{f.form} filed {f.filed}", locator=exp.section_title, **{**base_prov, "url": hit["url"]}),
                                 text=hit["quote"], key=f"q:{cat}:{i}")
            hit["evidence_id"] = qid
            run.ev[f"q:{cat}:{i}"] = qid
    for gm in exp.geographies[:8]:
        run.ev[f"geo:{gm.region}"] = run.ledger.add("number", f"Mentions of {gm.region} in {exp.section_title}",
                                                    Provenance(title=f"{f.form} filed {f.filed}", locator=exp.section_title, **{**base_prov, "url": gm.url or f.url}),
                                                    value=gm.mentions, unit="mentions", text=None, key=f"geo:{gm.region}")


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

BASE_PLAN = [
    {"agent": "graph", "tool": "sec.company_profile", "why": "Identity, filings and 6 fiscal years of XBRL fundamentals"},
    {"agent": "graph", "tool": "sec.risk_factors", "why": "Latest 10-K/20-F risk-factor section, sentence-split for verbatim quoting"},
    {"agent": "graph", "tool": "exposure.tag_4w", "why": "Tag Watts/Wires/Water/Weaponized + geographies, minerals, chokepoints, counterparties"},
    {"agent": "graph", "tool": "usgs.minerals", "why": "Country concentration (HHI) of minerals named in the filing or implied by the sector"},
    {"agent": "graph", "tool": "portwatch.chokepoints", "why": "Live AIS transit counts vs 365-day baseline on the company's trade routes"},
    {"agent": "graph", "tool": "csl.screen", "why": "Screen named counterparties against the US Consolidated Screening List"},
    {"agent": "graph", "tool": "comtrade.import_dependency", "why": "US import concentration for the sector's key HS codes"},
    {"agent": "simulation", "tool": "sandbox.stress_mc", "why": "Seeded Monte Carlo of revenue/FCF shock under the scenario sliders"},
    {"agent": "synthesizer", "tool": "memo.draft", "why": "KIC investment-committee memo, every figure tagged to evidence"},
    {"agent": "redteam", "tool": "verify.claims", "why": "Reject any number/quote not in the evidence ledger; bear-case critique; replan if needed"},
]


def orchestrate(run: Run, row: Dict) -> Dict[str, Any]:
    plan = {"steps": [dict(s) for s in BASE_PLAN], "hypotheses": [], "focus": [], "extra_entities": [], "extra_minerals": []}
    sc = run.scenario
    if run.use_llm:
        msg = [
            {"role": "system", "content": (
                "You are the Orchestrator of Geo-Alpha Sentinel, a multi-agent system that helps Korea Investment Corporation (KIC) "
                "assess how physical chokepoints (4W: Watts power, Wires copper/grid, Water, Weaponized resources/export controls) threaten a holding. "
                "Plan the analysis. Output one JSON object only.")},
            {"role": "user", "content": json.dumps({
                "company": row["name"], "ticker": row["ticker"],
                "scenario": {"taiwan": sc.taiwan, "china_export": sc.china_export, "hormuz": sc.hormuz, "malacca": sc.malacca, "horizon": sc.horizon},
                "analyst_question": sc.question,
                "available_tools": [s["tool"] for s in BASE_PLAN],
                "return_schema": {
                    "hypotheses": "2-3 falsifiable hypotheses about this company's chokepoint exposure (English, <= 25 words each)",
                    "focus": "list of 4W categories or scenario channels to emphasise",
                    "extra_entities": "up to 4 supplier/customer company names worth sanctions screening (only well-known real counterparties)",
                    "extra_minerals": "up to 3 USGS mineral ids (rare-earths, gallium, germanium, graphite, lithium, cobalt, nickel, copper, tungsten, antimony, helium, tantalum, silicon) the sector depends on",
                    "step_notes": "object mapping tool name -> one-line reason specific to this company",
                }}, ensure_ascii=False)},
        ]
        out = run.call_llm_json(msg, llm.FAST, max_tokens=900)
        if out:
            plan["hypotheses"] = [str(h)[:200] for h in out.get("hypotheses", [])][:3]
            plan["focus"] = [str(f)[:40] for f in out.get("focus", [])][:6]
            plan["extra_entities"] = [str(e)[:60] for e in out.get("extra_entities", [])][:4]
            plan["extra_minerals"] = [str(m).lower()[:20] for m in out.get("extra_minerals", [])][:3]
            notes = out.get("step_notes", {}) or {}
            for s in plan["steps"]:
                if isinstance(notes, dict) and s["tool"] in notes:
                    s["why"] = str(notes[s["tool"]])[:200]
    if not plan["hypotheses"]:
        top = max(("taiwan", sc.taiwan), ("china_export", sc.china_export), ("hormuz", sc.hormuz), ("malacca", sc.malacca), key=lambda x: x[1])
        plan["hypotheses"] = [
            f"{row['ticker']} revenue depends on a supply path that crosses the {CHANNEL_KO[top[0]]} channel stressed most in this scenario.",
            f"{row['ticker']}'s risk factors disclose export-control or critical-mineral dependencies that a disruption would activate.",
        ]
    return plan


# ---------------------------------------------------------------------------
# Synthesizer (LLM or template) and Red-Team
# ---------------------------------------------------------------------------

def _fact_sheet(run: Run) -> Tuple[List[Dict], List[Dict]]:
    facts, quotes = [], []
    for e in run.ledger.items:
        if e.kind == "quote":
            quotes.append({"id": e.id, "label": e.label, "text": e.text})
        elif e.value is not None:
            if e.unit == "USD":
                shown = fmt_usd(e.value)
            elif e.unit == "%":
                shown = f"{e.value:.1f}%"
            else:
                shown = f"{e.value:.1f} {e.unit or ''}".strip() if not float(e.value).is_integer() else f"{int(e.value)} {e.unit or ''}".strip()
            facts.append({"id": e.id, "label": e.label, "value": shown})
    return facts, quotes


def _recommendation(mets: Dict[str, Metric]) -> str:
    cpi, gpi, srs, far = (mets[c].value for c in ("CPI", "GPI", "SRS", "FAR"))
    if srs >= 60 or (cpi >= 60 and gpi >= 60):
        return "REDUCE"
    if srs >= 40 or gpi >= 55 or cpi >= 55:
        return "HEDGE"
    if far >= 65:
        return "ACCUMULATE"
    return "HOLD"


HEDGE_THEMES = {
    "taiwan": ("미국·일본 첨단 파운드리/패키징 대체 캐파", "대만 의존 공정의 이중화(Arizona·Kumamoto 등)에 투자하는 인프라·장비 노출로 대만 채널 충격을 상쇄"),
    "china_export": ("비중국 핵심광물 정제·영구자석 공급망", "갈륨·희토류 등 중국 점유율이 높은 광물의 동맹국 정제 캐파(호주·미국·일본)로 수출통제 충격 헤지"),
    "hormuz": ("에너지 조달 다변화(LNG·전력 인프라)", "중동 해상로 의존 에너지 비용 충격을 북미 LNG·송배전 인프라 자산으로 상쇄"),
    "malacca": ("해상물류 우회·역내 생산 거점", "말라카 경유 물동량 차질에 대비한 인도·멕시코 등 니어쇼어링 생산거점 노출"),
    "watts": ("전력망 장비(변압기·송전) 공급망", "AI·데이터센터 전력 병목의 수혜 영역인 변압기·송배전 설비 투자로 Watts 리스크를 알파로 전환"),
}


def template_memo(run: Run, p: CompanyProfile, exp: Exposure, mets: Dict[str, Metric], stress, chans: Dict[str, float]) -> Dict[str, Any]:
    ev = run.ev
    rec = _recommendation(mets)
    cpi, gpi, srs, far = mets["CPI"], mets["GPI"], mets["SRS"], mets["FAR"]
    tag = lambda k: f"[{ev[k]}]" if k in ev else ""  # noqa: E731
    top_cat = max(exp.categories.items(), key=lambda kv: kv[1]["score"])[0] if exp.categories else "weaponized"
    top_chan = max(chans.items(), key=lambda kv: kv[1])[0]
    fy = p.fundamentals["revenue"][-1].fy if p.fundamentals.get("revenue") else None
    summary = (
        f"{p.name}({p.ticker})의 CPI {cpi.value:.1f}{tag('metric:CPI')}, GPI {gpi.value:.1f}{tag('metric:GPI')}, "
        f"SRS {srs.value:.1f}{tag('metric:SRS')}, FAR {far.rating}({far.value:.1f}){tag('metric:FAR')}. "
        f"시나리오({run.scenario.horizon})에서 FCF 20%{tag('const:dd')} 이상 훼손 확률은 {stress.prob_fcf_drawdown_gt_20 * 100:.1f}%{tag('stress:p_dd')}이며, "
        f"가장 큰 노출 경로는 {CHANNEL_KO[top_chan]}입니다. 투자심의 권고: {rec}."
    )
    sections = []
    fin = []
    if "xbrl:revenue" in ev:
        fin.append(f"FY{fy} 매출 {fmt_usd(p.fundamentals['revenue'][-1].value)}{tag('xbrl:revenue')}")
    if "drv:gross_margin_pct" in ev:
        fin.append(f"매출총이익률 {run.ledger.get(ev['drv:gross_margin_pct']).value:.1f}%{tag('drv:gross_margin_pct')}")
    if "drv:fcf" in ev:
        fin.append(f"잉여현금흐름 {fmt_usd(run.ledger.get(ev['drv:fcf']).value)}{tag('drv:fcf')}")
    if fin:
        sections.append({"heading": "1. 기초 체력 (SEC XBRL)", "body": ", ".join(fin) + ". 이 수치는 SEC에 제출된 XBRL 재무데이터에서 직접 가져왔습니다."})
    lines = []
    for cat in ("watts", "wires", "water", "weaponized"):
        d = exp.categories.get(cat, {})
        lines.append(f"{CAT_KO[cat]} {d.get('score', 0):.1f}점{tag('exp:' + cat)}(리스크 문장 {d.get('hits', 0)}개{tag('exph:' + cat)})")
    body = f"{exp.filing.form}({exp.filing.filed}) {exp.section_title}을 문장 단위로 태깅한 결과: " + ", ".join(lines) + "."
    if f"q:{top_cat}:0" in ev:
        q = re.sub(r"[“”\"]", "'", run.ledger.get(ev[f"q:{top_cat}:0"]).text)
        body += f" 대표 원문: “{q[:300].rstrip()}”{tag(f'q:{top_cat}:0')}"
    sections.append({"heading": "2. 4W 물리적 초크포인트 노출 (공시 원문 근거)", "body": body})
    geo_bits = [f"{g.region} {g.mentions}회{tag('geo:' + g.region)}" for g in exp.geographies[:4] if f"geo:{g.region}" in ev]
    geo_txt = ("리스크 섹션의 지역 언급은 " + ", ".join(geo_bits) + "입니다. ") if geo_bits else ""
    sections.append({"heading": "3. 지정학·수출통제", "body": geo_txt + f"수출통제·제재·관세 언어와 중국·대만 집중도를 결합한 GPI는 {gpi.value:.1f}{tag('metric:GPI')}입니다."})
    chan_txt = ", ".join(f"{CHANNEL_KO[k]} {v:.2f}{tag('chan:' + k)}" for k, v in sorted(chans.items(), key=lambda kv: -kv[1]))
    sections.append({"heading": "4. 시나리오 스트레스 (샌드박스 몬테카를로)", "body": (
        f"{stress.paths:,}개 경로{tag('const:paths')}(시드 {stress.seed}{tag('const:seed')})로 계산한 매출 충격 중앙값은 {stress.revenue_shock_pct['p50']:.1f}%{tag('stress:rev_p50')}, "
        f"5백분위(p05) 꼬리는 {stress.revenue_shock_pct['p05']:.1f}%{tag('stress:rev_p05')}입니다. 채널별 노출도(0–1): {chan_txt}. "
        f"계산 코드는 그대로 공개되며 같은 입력이면 같은 결과가 나옵니다.")})
    claims = [
        {"text": f"CPI {cpi.value:.1f}{tag('metric:CPI')}", "evidence_ids": [ev["metric:CPI"]]},
        {"text": f"P(FCF 20%{tag('const:dd')} 이상 훼손) {stress.prob_fcf_drawdown_gt_20 * 100:.1f}%{tag('stress:p_dd')}", "evidence_ids": [ev["stress:p_dd"], ev["const:dd"]]},
        {"text": f"{CAT_KO[top_cat]} 노출 점수 {exp.categories[top_cat]['score']:.1f}{tag('exp:' + top_cat)}", "evidence_ids": [ev["exp:" + top_cat]]},
    ]
    if "xbrl:revenue" in ev:
        claims.append({"text": f"FY{fy} 매출 {fmt_usd(p.fundamentals['revenue'][-1].value)}{tag('xbrl:revenue')}", "evidence_ids": [ev["xbrl:revenue"]]})
    hedges = []
    for k, _ in sorted(chans.items(), key=lambda kv: -kv[1])[:2]:
        t, r = HEDGE_THEMES[k]
        hedges.append({"theme": t, "rationale": r})
    if exp.categories.get("watts", {}).get("score", 0) >= 40:
        t, r = HEDGE_THEMES["watts"]
        hedges.append({"theme": t, "rationale": r})
    return {
        "title": f"{p.name} — 2027-2028 초크포인트 리스크 투자심의 메모",
        "recommendation": rec,
        "summary": summary,
        "sections": sections,
        "claims": claims,
        "hedges": hedges,
    }


SYNTH_SYSTEM = (
    "You are the Sovereign Alpha Synthesizer of Geo-Alpha Sentinel, writing an investment-committee memo for Korea Investment Corporation (KIC) "
    "in Korean (professional, concise, no hype). Hard rules:\n"
    "1. Use ONLY numbers that appear in FACTS, written exactly as given in their `value` field (you may drop the unit word). Never compute new numbers.\n"
    "2. Put the evidence tag immediately after EVERY number, e.g. 'FY2026 매출 $215.9B[E1], 매출총이익률 71.1%[E68]'. A number without its own adjacent tag is rejected.\n"
    "3. Filing text may only be quoted by copying a QUOTES `text` substring exactly, inside “ ”, followed by its tag.\n"
    "4. Recommendation must be one of REDUCE, HEDGE, HOLD, ACCUMULATE and must follow the METRICS and the policy given.\n"
    "5. Hedge ideas are themes (asset classes/supply-chain segments), not individual stock picks.\n"
    "Return one JSON object: {title, recommendation, summary, sections:[{heading, body}], claims:[{text, evidence_ids}], hedges:[{theme, rationale}]}. "
    "4-5 sections; 4-7 claims, each claim one sentence with at least one tag."
)


def llm_memo(run: Run, p: CompanyProfile, mets: Dict[str, Metric], plan: Dict, feedback: Optional[List[str]] = None) -> Optional[Dict]:
    facts, quotes = _fact_sheet(run)
    policy = {"REDUCE": "SRS>=60 or (CPI>=60 and GPI>=60)", "HEDGE": "SRS>=40 or GPI>=55 or CPI>=55", "ACCUMULATE": "FAR>=65", "HOLD": "otherwise",
              "computed_by_policy": _recommendation(mets)}
    user = {
        "company": f"{p.name} ({p.ticker})",
        "scenario": run.scenario.model_dump(),
        "analyst_question": run.scenario.question,
        "hypotheses": plan.get("hypotheses", []),
        "METRICS": {c: {"value": m.value, "rating": m.rating, "evidence_id": run.ev.get(f"metric:{c}")} for c, m in mets.items()},
        "policy": policy,
        "FACTS": facts,
        "QUOTES": quotes[:24],
    }
    msgs = [{"role": "system", "content": SYNTH_SYSTEM}, {"role": "user", "content": json.dumps(user, ensure_ascii=False)}]
    if feedback:
        msgs.append({"role": "user", "content": "The Red-Team verifier rejected these items. Rewrite the whole memo fixing them (remove or correct each):\n- " + "\n- ".join(feedback)})
    return run.call_llm_json(msgs, llm.MAIN, max_tokens=3500)


def red_team_critique(run: Run, memo: Dict) -> List[Dict[str, str]]:
    facts, quotes = _fact_sheet(run)
    msgs = [
        {"role": "system", "content": (
            "You are the adversarial Red-Team agent. Attack the memo's thesis from the opposite side (if it says REDUCE, argue why exposure may be overstated; "
            "if HOLD/ACCUMULATE, argue the hidden risk). Korean. Use only FACTS/QUOTES with [E#] tags, no new numbers. "
            "Return JSON {counterpoints:[{text, evidence_ids}]} with 2-3 items, one sentence each.")},
        {"role": "user", "content": json.dumps({"memo": {k: memo.get(k) for k in ("recommendation", "summary")}, "FACTS": facts, "QUOTES": quotes[:16]}, ensure_ascii=False)},
    ]
    out = run.call_llm_json(msgs, llm.FAST, max_tokens=900)
    return (out or {}).get("counterpoints", []) if isinstance(out, dict) else []


def template_counterpoints(run: Run, exp: Exposure, chans: Dict[str, float], stress) -> List[Dict]:
    ev = run.ev
    low = min(chans.items(), key=lambda kv: kv[1])
    pts = [
        {"text": f"리스크 문장 밀도는 공시 작성 관행의 영향을 받으므로 노출 점수{('[' + ev['exp:weaponized'] + ']') if 'exp:weaponized' in ev else ''}가 실제 매출 의존도를 과대평가할 수 있습니다.",
         "evidence_ids": [ev["exp:weaponized"]] if "exp:weaponized" in ev else []},
        {"text": f"네 채널 중 {CHANNEL_KO[low[0]]} 노출도가 {low[1]:.2f}[{ev['chan:' + low[0]]}]로 가장 낮아, 해당 경로 단독 충격의 손실 기여는 상대적으로 작습니다.",
         "evidence_ids": [ev["chan:" + low[0]]]},
    ]
    return pts


# ---------------------------------------------------------------------------
# Main generator
# ---------------------------------------------------------------------------

def run_analysis(ticker: str, scenario: Scenario) -> Iterator[Union[AgentEvent, AnalysisResult]]:
    run = Run(ticker.strip().upper(), scenario)
    run.use_llm = llm.enabled() and llm.take_run_budget()
    yield run.emit("system", "thought", f"Engine: {llm.MAIN if run.use_llm else 'deterministic planner + template synthesizer'}",
                   data={"llm": run.use_llm, "model": llm.MAIN if run.use_llm else None})

    row = sec.resolve(run.ticker)
    if row is None:
        yield run.emit("system", "error", f"{run.ticker} is not an SEC registrant ticker", detail="Try NVDA, TSM, TSLA, AAPL, ASML, MU, INTC.")
        return

    # ---- Orchestrator -----------------------------------------------------
    yield run.emit("orchestrator", "thought", f"Mission: chokepoint & geopolitical stress test of {row['name']} ({row['ticker']})",
                   detail=f"Scenario Taiwan {scenario.taiwan:.0%} · China export controls {scenario.china_export:.0%} · Hormuz {scenario.hormuz:.0%} · Malacca {scenario.malacca:.0%} · horizon {scenario.horizon}")
    plan = orchestrate(run, row)
    yield run.emit("orchestrator", "plan", f"Plan: {len(plan['steps'])} steps, {len(plan['hypotheses'])} hypotheses", data=plan)

    # ---- Graph agent: evidence gathering ---------------------------------
    t = time.perf_counter()
    yield run.emit("graph", "tool_call", f"sec.company_profile({row['ticker']})", data={"tool": "sec.company_profile", "args": {"ticker": row["ticker"]}})
    profile = sec.company_profile(row["ticker"])
    _xbrl_evidence(run, profile)
    yield run.emit("graph", "tool_result", f"{profile.name} · SIC {profile.sic} {profile.sic_description or ''} · {len(profile.fundamentals)} XBRL concepts",
                   data={"tool": "sec.company_profile", "summary": {"latest_annual": profile.latest_annual.model_dump() if profile.latest_annual else None}, "evidence_ids": [v for k, v in run.ev.items() if k.startswith("xbrl:")], "ms": int((time.perf_counter() - t) * 1000)})
    if profile.latest_annual is None:
        yield run.emit("system", "error", "No 10-K / 20-F on EDGAR for this registrant yet")
        return

    t = time.perf_counter()
    yield run.emit("graph", "tool_call", f"sec.risk_factors({profile.latest_annual.form} {profile.latest_annual.filed})", data={"tool": "sec.risk_factors", "args": {"url": profile.latest_annual.url}})
    section = filings.risk_section(profile.latest_annual)
    exposure = analyze_exposure(profile.ticker, section)
    name_tokens = {w.lower() for w in re.findall(r"[A-Za-z]{3,}", profile.name)}
    exposure.entities_named = [e for e in exposure.entities_named if not ({w.lower() for w in re.findall(r"[A-Za-z]{3,}", e)} & name_tokens)]
    _exposure_evidence(run, exposure)
    yield run.emit("graph", "tool_result", f"{section.title}: {len(section.text):,} chars, {len(section.sentences)} sentences tagged",
                   data={"tool": "exposure.tag_4w", "summary": {c: {"score": d["score"], "hits": d["hits"]} for c, d in exposure.categories.items()},
                         "geographies": [(g.region, g.mentions) for g in exposure.geographies[:6]], "minerals": exposure.minerals_mentioned,
                         "chokepoints": exposure.chokepoints_mentioned, "entities": exposure.entities_named,
                         "evidence_ids": [v for k, v in run.ev.items() if k.startswith(("exp:", "q:", "geo:"))], "ms": int((time.perf_counter() - t) * 1000)})

    graph_triples = _graph_triples(run, profile, exposure, section)
    yield run.emit("graph", "tool_result", f"Supply-chain knowledge graph: {len(graph_triples)} triples", data={"tool": "graph.triples", "summary": graph_triples[:12], "evidence_ids": []})

    # minerals
    minerals_rel: List[Tuple[Mineral, str]] = []
    try:
        from sentinel.sources import minerals as minerals_src

        _, all_minerals = minerals_src.get_minerals()
        minerals_rel = M.relevant_minerals(profile, exposure, all_minerals)
        for mid in plan.get("extra_minerals", []):
            hit = [m for m in all_minerals if m.id == mid]
            if hit and mid not in [m.id for m, _ in minerals_rel]:
                minerals_rel.append((hit[0], "sector"))
        yield run.emit("graph", "tool_call", f"usgs.minerals({', '.join(m.id for m, _ in minerals_rel) or 'none'})", data={"tool": "usgs.minerals", "args": {"ids": [m.id for m, _ in minerals_rel]}})
        for m, src in minerals_rel:
            run.ev[f"min:{m.id}"] = run.ledger.add("number", f"{m.name}: country concentration HHI ({m.year}, {src})", m.provenance, value=m.hhi, unit="HHI",
                                                   data={"top_country": m.top_country, "top_share_pct": m.top_share_pct, "china_share_pct": m.china_share_pct}, key=f"min:{m.id}")
            if m.china_share_pct is not None:
                run.ev[f"minc:{m.id}"] = run.ledger.add("number", f"{m.name}: China share of world {m.measure.lower()} ({m.year})", m.provenance, value=m.china_share_pct, unit="%", key=f"minc:{m.id}")
        yield run.emit("graph", "tool_result", f"{len(minerals_rel)} minerals · max HHI {max([m.hhi or 0 for m, _ in minerals_rel] or [0]):,.0f}",
                       data={"tool": "usgs.minerals", "summary": [{"id": m.id, "hhi": m.hhi, "china_share_pct": m.china_share_pct, "source": s} for m, s in minerals_rel], "evidence_ids": [run.ev[k] for k in run.ev if k.startswith("min")]})
    except Exception as exc:  # snapshot missing
        yield run.emit("graph", "error", "usgs.minerals unavailable", detail=str(exc)[:200])

    # chokepoints
    chokepoints: List[Chokepoint] = []
    try:
        from sentinel.sources import portwatch

        t = time.perf_counter()
        yield run.emit("graph", "tool_call", "portwatch.chokepoints(28 straits, 500 days)", data={"tool": "portwatch.chokepoints", "args": {}})
        as_of, chokepoints = portwatch.get_chokepoints()
        routes = M.relevant_routes(exposure)
        for c in chokepoints:
            if c.id in routes and c.delta_pct is not None:
                run.ev[f"chk:{c.id}"] = run.ledger.add("number", f"{c.name}: 7-day mean transits vs 365-day baseline (%)", c.provenance, value=round(c.delta_pct, 2), unit="%",
                                                       data={"mean_7d": c.mean_7d, "baseline_365d": c.baseline_365d, "latest": c.latest.model_dump() if c.latest else None}, key=f"chk:{c.id}")
                if c.mean_7d is not None:
                    run.ev[f"chkn:{c.id}"] = run.ledger.add("number", f"{c.name}: mean daily transits, last 7 days", c.provenance, value=round(c.mean_7d, 1), unit="ships/day", key=f"chkn:{c.id}")
        yield run.emit("graph", "tool_result", f"PortWatch as of {as_of}: {len(routes)} relevant routes",
                       data={"tool": "portwatch.chokepoints", "summary": [{"id": c.id, "name": c.name, "delta_pct": c.delta_pct, "weight": round(routes.get(c.id, 0), 3)} for c in chokepoints if c.id in routes],
                             "evidence_ids": [run.ev[k] for k in run.ev if k.startswith("chk")], "ms": int((time.perf_counter() - t) * 1000)})
    except Exception as exc:
        yield run.emit("graph", "error", "portwatch.chokepoints unavailable", detail=str(exc)[:200])

    # sanctions
    screens: List[SanctionsScreen] = []
    try:
        from sentinel.sources import sanctions as sanctions_src

        names = list(dict.fromkeys(exposure.entities_named[:6] + plan.get("extra_entities", [])))[:8]
        if names:
            yield run.emit("graph", "tool_call", f"csl.screen({len(names)} counterparties)", data={"tool": "csl.screen", "args": {"names": names}})
            screens = sanctions_src.screen_many(names)
            for s in screens:
                run.ev[f"sanc:{s.query}"] = run.ledger.add("number", f"Consolidated Screening List matches for '{s.query}'", s.provenance, value=len(s.matches), unit="matches",
                                                           data=[m.model_dump() for m in s.matches[:3]], key=f"sanc:{s.query}")
            yield run.emit("graph", "tool_result", f"{sum(1 for s in screens if s.matches)} of {len(screens)} counterparties match US screening lists",
                           data={"tool": "csl.screen", "summary": [{"query": s.query, "matches": [m.matched_name for m in s.matches[:2]]} for s in screens], "evidence_ids": [run.ev[k] for k in run.ev if k.startswith("sanc")]})
    except Exception as exc:
        yield run.emit("graph", "error", "csl.screen unavailable", detail=str(exc)[:200])

    # comtrade
    try:
        from sentinel.sources import comtrade

        hs_codes = SECTOR_HS.get((profile.sic or "")[:3], [])
        for hs in hs_codes[:2]:
            yield run.emit("graph", "tool_call", f"comtrade.import_dependency(HS {hs}, reporter=USA)", data={"tool": "comtrade.import_dependency", "args": {"hs": hs}})
            dep = comtrade.import_dependency(hs)
            top = (dep.get("partners") or [{}])[0]
            if top:
                run.ev[f"hs:{hs}"] = run.ledger.add("number", f"US imports HS {hs} ({dep.get('description', '')}) {dep.get('year')}: top partner {top.get('partner')} share",
                                                    dep["provenance"] if isinstance(dep["provenance"], Provenance) else Provenance(**dep["provenance"]),
                                                    value=round(top.get("share_pct", 0), 2), unit="%", data=dep.get("partners", [])[:5], key=f"hs:{hs}")
            yield run.emit("graph", "tool_result", f"HS {hs}: top partner {top.get('partner')} {top.get('share_pct', 0):.1f}% · HHI {dep.get('hhi', 0):,.0f}",
                           data={"tool": "comtrade.import_dependency", "summary": dep.get("partners", [])[:5], "evidence_ids": [run.ev.get(f"hs:{hs}")]})
    except Exception as exc:
        yield run.emit("graph", "error", "comtrade.import_dependency unavailable", detail=str(exc)[:200])

    # ---- Simulation agent -------------------------------------------------
    for key, label, val, unit in (("const:dd", "FCF drawdown threshold used by SRS", 20, "%"), ("const:paths", "Monte Carlo paths", MC_PATHS, "paths"), ("const:seed", "Monte Carlo random seed", MC_SEED, "seed")):
        run.ev[key] = run.ledger.model_value(label, val, unit, "stress program v1", key=key)
    yield run.emit("simulation", "tool_call", "sandbox.derived_fundamentals()", data={"tool": "sandbox.run", "args": {"inputs": "latest-FY XBRL facts"}})
    derived, ev_derived = _derived_numbers(run, profile)
    yield ev_derived
    chans = M.channel_exposures(profile, exposure, [m for m, _ in minerals_rel])
    for k, v in chans.items():
        run.ev[f"chan:{k}"] = run.ledger.model_value(f"Channel exposure: {CHANNEL_KO[k]}", v, "0-1", "channel exposure", key=f"chan:{k}")
    yield run.emit("simulation", "thought", "Channel exposures from filing evidence", detail=", ".join(f"{k} {v:.2f}" for k, v in chans.items()), data=chans)

    rev = M.latest(profile, "revenue")
    gm = (derived.get("gross_margin_pct") or 50.0) / 100.0
    fcf = derived.get("fcf") if derived.get("fcf") is not None else (rev or 0) * 0.1
    base_year = profile.fundamentals["revenue"][-1].fy if profile.fundamentals.get("revenue") else 2025
    yield run.emit("simulation", "tool_call", "sandbox.stress_mc(seed=42)", data={"tool": "sandbox.stress_mc", "args": {"exposure": chans, "severity": scenario.model_dump(exclude={"question"})}})
    stress = run_stress(scenario, chans, base_revenue=rev or 1.0, gross_margin=gm, base_fcf=fcf, base_year=base_year)
    sb_prov = Provenance(source="sandbox", title=f"Sandbox Monte Carlo ({stress.paths:,} paths, seed {stress.seed})", url="https://github.com/cjw0076/geo-alpha-sentinel/blob/main/sentinel/analytics/stress.py",
                         retrieved_at=utcnow_iso(), license="Apache-2.0", locator="stress program v1")
    run.ev["stress:p_dd"] = run.ledger.add("number", "P(FCF drawdown > 20%) under scenario", sb_prov, value=round(stress.prob_fcf_drawdown_gt_20 * 100, 2), unit="%", key="stress:p_dd")
    run.ev["stress:rev_p50"] = run.ledger.add("number", "Revenue shock, median path", sb_prov, value=stress.revenue_shock_pct["p50"], unit="%", key="stress:rev_p50")
    run.ev["stress:rev_p05"] = run.ledger.add("number", "Revenue shock, 5th percentile (tail)", sb_prov, value=stress.revenue_shock_pct["p05"], unit="%", key="stress:rev_p05")
    run.ev["stress:fcf_p50"] = run.ledger.add("number", "FCF shock, median path", sb_prov, value=stress.fcf_shock_pct["p50"], unit="%", key="stress:fcf_p50")
    if rev:
        run.ev["stress:rev_at_risk_p05"] = run.ledger.add("number", "Revenue at risk, 5th percentile path", sb_prov, value=round(-stress.revenue_shock_pct["p05"] / 100 * rev, 0), unit="USD", key="stress:rev_at_risk_p05")
    yield run.emit("simulation", "tool_result", f"Revenue shock p50 {stress.revenue_shock_pct['p50']:.1f}% · p05 {stress.revenue_shock_pct['p05']:.1f}% · P(FCF↓>20%) {stress.prob_fcf_drawdown_gt_20:.1%}",
                   data={"tool": "sandbox.stress_mc", "summary": {"revenue_shock_pct": stress.revenue_shock_pct, "fcf_shock_pct": stress.fcf_shock_pct, "prob_fcf_drawdown_gt_20": stress.prob_fcf_drawdown_gt_20},
                         "evidence_ids": [run.ev[k] for k in run.ev if k.startswith("stress:")], "ms": stress.inputs.get("sandbox_ms")})

    # ---- Metrics ------------------------------------------------------------
    mets_list = M.compute_metrics(profile, exposure, minerals_rel, chokepoints, screens, stress, run.ledger, run.ev)
    mets = {m.code: m for m in mets_list}
    for m in mets_list:
        run.ev[f"metric:{m.code}"] = run.ledger.model_value(f"{m.code} — {m.name}", m.value, "0-100", m.code, data={"rating": m.rating, "formula": m.formula}, key=f"metric:{m.code}")
        yield run.emit("simulation", "metric", f"{m.code} {m.value:.1f}{(' ' + m.rating) if m.rating else ''}", detail=m.formula, data=m.model_dump())

    # ---- Synthesizer + Red-Team loop -------------------------------------
    feedback: Optional[List[str]] = None
    memo_dict: Optional[Dict] = None
    replans = 0
    claims: List[Claim] = []
    for attempt in range(3):
        yield run.emit("synthesizer", "tool_call", "memo.draft" + (f" (revision {attempt})" if attempt else ""), data={"tool": "memo.draft", "args": {"engine": llm.MAIN if run.use_llm else "template", "facts": len(run.ledger.items)}})
        draft = llm_memo(run, profile, mets, plan, feedback) if run.use_llm else None
        source = "llm"
        if not draft or not isinstance(draft.get("sections"), list):
            draft = template_memo(run, profile, exposure, mets, stress, chans)
            source = "template"
        yield run.emit("synthesizer", "memo", f"Draft memo ({source}): {draft.get('recommendation')} — {len(draft.get('sections', []))} sections, {len(draft.get('claims', []))} claims",
                       data={"title": draft.get("title"), "recommendation": draft.get("recommendation")})

        yield run.emit("redteam", "tool_call", "verify.claims(ledger)", data={"tool": "verify.claims", "args": {"claims": len(draft.get("claims", [])), "sections": len(draft.get("sections", []))}})
        claims = verifier.verify_claims(draft.get("claims", []), run.ledger)
        problems = verifier.verify_sections(draft.get("sections", []), run.ledger)
        ok_sum, why = verifier.check_text(draft.get("summary", ""), run.ledger)
        if not ok_sum:
            problems.append({"heading": "summary", "reason": why or ""})
        rec_ok = draft.get("recommendation") == _recommendation(mets)
        if not rec_ok:
            problems.append({"heading": "recommendation", "reason": f"recommendation {draft.get('recommendation')} violates policy (expected {_recommendation(mets)})"})
        rejected = [{"text": c.text, "reason": c.reason} for c in claims if not c.verified] + [{"text": p_["heading"], "reason": p_["reason"]} for p_ in problems]
        checked = len(claims) + len(draft.get("sections", [])) + 2
        yield run.emit("redteam", "verification", f"✓ {checked - len(rejected)} checked · ✗ {len(rejected)} rejected", data={"passed": not rejected, "checked": checked, "rejected": rejected})
        memo_dict = draft
        if not rejected:
            break
        if source == "template" or attempt == 2:
            # Last resort: keep verified claims only; replace failing sections with the template's.
            tmpl = template_memo(run, profile, exposure, mets, stress, chans)
            bad = {p_["heading"] for p_ in problems}
            memo_dict["sections"] = [s for s in draft.get("sections", []) if s.get("heading") not in bad] or tmpl["sections"]
            if "summary" in bad:
                memo_dict["summary"] = tmpl["summary"]
            if "recommendation" in bad:
                memo_dict["recommendation"] = tmpl["recommendation"]
            break
        replans += 1
        feedback = [f"{r['text'][:120]} → {r['reason']}" for r in rejected]
        yield run.emit("orchestrator", "replan", f"Replan {replans}: {len(rejected)} items failed verification → back to Synthesizer", data={"attempt": replans, "reason": feedback[:6]})

    # Bear-case critique (also verified)
    cps = red_team_critique(run, memo_dict) if run.use_llm else []
    if not cps:
        cps = template_counterpoints(run, exposure, chans, stress)
    cp_claims = verifier.verify_claims(cps, run.ledger)
    good_cps = [c for c in cp_claims if c.verified]
    yield run.emit("redteam", "verification", f"Bear-case: {len(good_cps)}/{len(cp_claims)} counterpoints grounded",
                   data={"passed": len(good_cps) == len(cp_claims), "checked": len(cp_claims), "rejected": [{"text": c.text, "reason": c.reason} for c in cp_claims if not c.verified]})
    if good_cps:
        memo_dict.setdefault("sections", []).append({"heading": "Red-Team 반대 논거", "body": " ".join(c.text for c in good_cps)})

    memo = Memo(
        title=str(memo_dict.get("title") or f"{profile.name} memo")[:200],
        recommendation=memo_dict.get("recommendation") if memo_dict.get("recommendation") in ("REDUCE", "HEDGE", "HOLD", "ACCUMULATE") else _recommendation(mets),
        summary=str(memo_dict.get("summary", "")),
        sections=[{"heading": str(s.get("heading", "")), "body": str(s.get("body", ""))} for s in memo_dict.get("sections", []) if isinstance(s, dict)],
        claims=claims + [c for c in cp_claims if c.verified],
        hedges=[{"theme": str(h.get("theme", "")), "rationale": str(h.get("rationale", ""))} for h in memo_dict.get("hedges", []) if isinstance(h, dict)][:4],
        language="ko",
    )
    yield run.emit("synthesizer", "memo", f"Final memo: {memo.recommendation}", data=memo.model_dump())

    engine = {
        "llm": llm.MAIN if run.use_llm else "deterministic",
        "llm_calls": run.llm_calls,
        "llm_ms": run.llm_ms,
        "replans": replans,
        "verified_claims": sum(1 for c in memo.claims if c.verified),
        "rejected_claims": sum(1 for c in claims if not c.verified),
        "evidence_items": len(run.ledger.items),
        "graph_triples": graph_triples,
        "exposure_by_channel": chans,
    }
    yield run.emit("system", "done", f"Run complete in {run.ms() / 1000:.1f}s", data={"ms": run.ms(), "engine": {k: v for k, v in engine.items() if k not in ("graph_triples",)}})
    yield AnalysisResult(
        run_id=run.run_id,
        ticker=profile.ticker,
        company=profile,
        scenario=scenario,
        exposure=exposure,
        chokepoints=chokepoints,
        minerals=[m for m, _ in minerals_rel],
        sanctions=screens,
        metrics=mets_list,
        stress=stress,
        memo=memo,
        evidence=run.ledger.items,
        events=run.events,
        engine=engine,
        generated_at=utcnow_iso(),
    )


# ---------------------------------------------------------------------------
# Graph triples (LLM triage over candidate sentences, deterministic fallback)
# ---------------------------------------------------------------------------

def _graph_triples(run: Run, p: CompanyProfile, exp: Exposure, section) -> List[Dict[str, Any]]:
    cands: List[Dict[str, Any]] = []
    for cat, d in exp.categories.items():
        for h in d.get("top", [])[:4]:
            cands.append({"sid": f"S{len(cands) + 1}", "category": cat, "term": h["term"], "text": h["quote"], "evidence_id": h.get("evidence_id")})
    triples: List[Dict[str, Any]] = []
    if run.use_llm and cands:
        msgs = [
            {"role": "system", "content": (
                "You are the 4W Supply-Chain Graph agent. From the numbered filing sentences, extract dependency triples. "
                "Each triple: {subject, relation (DEPENDS_ON|SOURCES_FROM|LOCATED_IN|EXPOSED_TO|RESTRICTED_BY), object, category (watts|wires|water|weaponized), region (or null), severity 1-5, sid}. "
                "Only use entities literally present in the sentence `sid`. Return JSON {triples:[...]} with at most 14 triples.")},
            {"role": "user", "content": json.dumps({"company": p.name, "sentences": [{"sid": c["sid"], "text": c["text"][:600]} for c in cands]}, ensure_ascii=False)},
        ]
        out = run.call_llm_json(msgs, llm.FAST, max_tokens=1400)
        by_sid = {c["sid"]: c for c in cands}
        for t in (out or {}).get("triples", []) if isinstance(out, dict) else []:
            c = by_sid.get(str(t.get("sid")))
            if not c:
                continue
            obj = str(t.get("object", ""))[:80]
            if obj and obj.lower().split()[0] not in c["text"].lower():
                continue  # object must be grounded in the cited sentence
            triples.append({"subject": str(t.get("subject", p.name))[:80], "relation": str(t.get("relation", "EXPOSED_TO"))[:20], "object": obj,
                            "category": t.get("category", c["category"]), "region": t.get("region"), "severity": int(t.get("severity", 3)) if str(t.get("severity", "3")).isdigit() else 3,
                            "evidence_id": c["evidence_id"]})
    if not triples:
        for c in cands:
            triples.append({"subject": p.name, "relation": "EXPOSED_TO", "object": c["term"], "category": c["category"], "region": None, "severity": 3, "evidence_id": c["evidence_id"]})
    return triples
