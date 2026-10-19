"""Public data contract for the Geo-Alpha Sentinel API.

Every number the service shows is an `Evidence` record (or is computed from
Evidence records by code in `sentinel.analytics`). Every Evidence record carries
a `Provenance` that points to a public primary source, so any figure on screen or
in a memo can be traced back to the document it came from.
"""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Provenance & evidence
# ---------------------------------------------------------------------------

SourceId = Literal[
    "sec_edgar",        # SEC EDGAR submissions / XBRL company facts / filing documents
    "imf_portwatch",    # IMF PortWatch daily chokepoint transits (AIS satellite)
    "usgs_mcs",         # USGS Mineral Commodity Summaries
    "trade_csl",        # US Trade.gov Consolidated Screening List
    "un_comtrade",      # UN Comtrade bilateral trade
    "fred",             # FRED (St. Louis Fed) series
    "nyfed_gscpi",      # NY Fed Global Supply Chain Pressure Index
    "sandbox",          # value computed by the deterministic sandbox from other evidence
    "model",            # value computed by Sentinel methodology code from other evidence
]


class Provenance(BaseModel):
    source: SourceId
    title: str = Field(..., description="Human-readable source title, e.g. 'NVIDIA CORP 10-K filed 2026-02-25'")
    url: str = Field(..., description="Primary-source URL; for filing quotes a #:~:text= fragment URL")
    retrieved_at: str = Field(..., description="ISO-8601 UTC time the data was fetched")
    license: str = Field(..., description="e.g. 'Public domain (U.S. Government work)'")
    locator: Optional[str] = Field(None, description="Section/table/series locator, e.g. 'Item 1A', 'us-gaap:Revenues FY2025'")


class Evidence(BaseModel):
    id: str = Field(..., description="Stable ledger id, e.g. 'E7'")
    kind: Literal["number", "quote", "series", "table"]
    label: str
    value: Optional[float] = None
    unit: Optional[str] = None
    text: Optional[str] = Field(None, description="Verbatim quote for kind='quote'")
    data: Optional[Any] = None
    provenance: Provenance


# ---------------------------------------------------------------------------
# Company & filings (DES / FA)
# ---------------------------------------------------------------------------

class FactPoint(BaseModel):
    fy: int
    fp: str = "FY"
    end: str
    value: float
    form: str
    accn: str
    filed: str


class FilingRef(BaseModel):
    form: str
    filed: str
    period: Optional[str] = None
    accession: str
    primary_document: str
    url: str


class CompanyProfile(BaseModel):
    ticker: str
    cik: str
    name: str
    sic: Optional[str] = None
    sic_description: Optional[str] = None
    exchanges: List[str] = []
    state_of_incorporation: Optional[str] = None
    hq_country: Optional[str] = Field(None, description="Business address state/country as reported to EDGAR, e.g. 'CA', 'TAIWAN'")
    fiscal_year_end: Optional[str] = None
    filer_category: Optional[str] = None
    latest_annual: Optional[FilingRef] = None
    # concept name -> yearly points (most recent last). Concepts:
    # revenue, gross_profit, operating_income, net_income, cfo, capex, ppe_net,
    # inventory, rd_expense, cash, total_assets
    fundamentals: Dict[str, List[FactPoint]] = {}
    currency: str = "USD"
    provenance: List[Provenance] = []


# ---------------------------------------------------------------------------
# 4W exposure (from the latest annual filing's risk-factor section)
# ---------------------------------------------------------------------------

FourW = Literal["watts", "wires", "water", "weaponized"]


class ExposureHit(BaseModel):
    category: FourW
    term: str = Field(..., description="Taxonomy term that matched, e.g. 'rare earth'")
    quote: str = Field(..., description="Verbatim sentence from the filing")
    section: str
    url: str = Field(..., description="Filing URL with #:~:text= fragment pointing at the quote")
    evidence_id: Optional[str] = None


class GeoMention(BaseModel):
    region: str = Field(..., description="e.g. 'Taiwan', 'China', 'Korea', 'Japan'")
    mentions: int
    sample_quote: Optional[str] = None
    url: Optional[str] = None


class Exposure(BaseModel):
    ticker: str
    filing: Optional[FilingRef] = None
    section_title: Optional[str] = None
    section_chars: int = 0
    categories: Dict[str, Dict[str, Any]] = Field(
        default_factory=dict,
        description="category -> {score 0-100, hits: int, top: [ExposureHit]}",
    )
    geographies: List[GeoMention] = []
    minerals_mentioned: List[str] = []
    chokepoints_mentioned: List[str] = []
    entities_named: List[str] = Field(default_factory=list, description="Third-party company names found in the section")


# ---------------------------------------------------------------------------
# Chokepoints (CHKP), minerals (MINR), sanctions (SANC), macro
# ---------------------------------------------------------------------------

class ChokepointPoint(BaseModel):
    date: str
    n_total: int
    capacity: Optional[float] = None


class Chokepoint(BaseModel):
    id: str
    name: str
    lat: float
    lon: float
    latest: Optional[ChokepointPoint] = None
    mean_7d: Optional[float] = None
    baseline_365d: Optional[float] = Field(None, description="Mean daily transits over the prior 365 days")
    delta_pct: Optional[float] = Field(None, description="(mean_7d / baseline_365d - 1) * 100")
    zscore: Optional[float] = None
    series: List[ChokepointPoint] = Field(default_factory=list, description="Last 120 days")
    provenance: Optional[Provenance] = None


class MineralCountry(BaseModel):
    country: str
    value: Optional[float]
    share_pct: Optional[float]


class Mineral(BaseModel):
    id: str = Field(..., description="slug e.g. 'rare-earths'")
    name: str
    year: int
    measure: str = Field(..., description="e.g. 'Mine production, rare-earth-oxide equivalent'")
    unit: str
    world_total: Optional[float]
    countries: List[MineralCountry]
    hhi: Optional[float] = Field(None, description="Herfindahl-Hirschman Index 0-10000 over country shares")
    top_country: Optional[str] = None
    top_share_pct: Optional[float] = None
    china_share_pct: Optional[float] = None
    us_net_import_reliance_pct: Optional[float] = None
    provenance: Provenance


class SanctionsMatch(BaseModel):
    name: str
    matched_name: str
    score: float
    list_source: str
    programs: Optional[str] = None
    country: Optional[str] = None
    entity_number: Optional[str] = None
    source_list_url: Optional[str] = None


class SanctionsScreen(BaseModel):
    query: str
    matches: List[SanctionsMatch]
    list_size: int
    snapshot_date: str
    provenance: Provenance


class MacroSeries(BaseModel):
    id: str
    name: str
    unit: str
    latest_date: str
    latest_value: float
    yoy_pct: Optional[float] = None
    series: List[Dict[str, Any]] = []
    provenance: Provenance


# ---------------------------------------------------------------------------
# Scenario, metrics, stress
# ---------------------------------------------------------------------------

class Scenario(BaseModel):
    taiwan: float = Field(0.5, ge=0, le=1, description="Taiwan Strait / Taiwan supply disruption severity")
    china_export: float = Field(0.5, ge=0, le=1, description="China critical-mineral export restriction severity")
    hormuz: float = Field(0.2, ge=0, le=1, description="Strait of Hormuz energy disruption severity")
    malacca: float = Field(0.2, ge=0, le=1, description="Strait of Malacca disruption severity")
    horizon: int = Field(2027, ge=2026, le=2030)
    question: Optional[str] = Field(None, max_length=600, description="Free-text analyst question")


class MetricComponent(BaseModel):
    name: str
    value: float
    weight: float
    evidence_ids: List[str] = []
    note: Optional[str] = None


class Metric(BaseModel):
    code: Literal["CPI", "GPI", "SRS", "FAR"]
    name: str
    value: float = Field(..., description="0-100 (FAR also has a letter rating)")
    rating: Optional[str] = None
    formula: str
    components: List[MetricComponent]


class StressResult(BaseModel):
    paths: int
    seed: int
    base_revenue: float
    base_fcf: Optional[float]
    revenue_shock_pct: Dict[str, float] = Field(..., description="p05, p50, p95, mean")
    fcf_shock_pct: Dict[str, float]
    prob_fcf_drawdown_gt_20: float
    histogram: Dict[str, List[float]] = Field(..., description="{'edges': [...], 'counts': [...]} of revenue shock %")
    code: str = Field(..., description="Exact Python source executed in the sandbox")
    inputs: Dict[str, Any]


# ---------------------------------------------------------------------------
# Agent run
# ---------------------------------------------------------------------------

AgentId = Literal["orchestrator", "graph", "simulation", "redteam", "synthesizer", "system"]


class AgentEvent(BaseModel):
    seq: int
    t_ms: int = Field(..., description="Milliseconds since run start")
    agent: AgentId
    type: Literal["plan", "tool_call", "tool_result", "thought", "metric", "verification", "replan", "memo", "error", "done"]
    title: str
    detail: Optional[str] = None
    data: Optional[Any] = None


class Claim(BaseModel):
    text: str
    evidence_ids: List[str]
    verified: bool
    reason: Optional[str] = None


class Memo(BaseModel):
    title: str
    recommendation: Literal["REDUCE", "HEDGE", "HOLD", "ACCUMULATE"]
    summary: str
    sections: List[Dict[str, str]] = Field(..., description="[{heading, body}] - body uses [E#] evidence tags")
    claims: List[Claim]
    hedges: List[Dict[str, str]] = Field(default_factory=list, description="[{theme, rationale}] friend-shoring ideas")
    language: str = "ko"


class AnalysisResult(BaseModel):
    run_id: str
    ticker: str
    company: CompanyProfile
    scenario: Scenario
    exposure: Exposure
    chokepoints: List[Chokepoint]
    minerals: List[Mineral]
    sanctions: List[SanctionsScreen]
    metrics: List[Metric]
    stress: StressResult
    memo: Memo
    evidence: List[Evidence]
    events: List[AgentEvent]
    engine: Dict[str, Any] = Field(..., description="{'llm': 'anthropic/claude-sonnet-5.5' | 'deterministic', 'replans': int, 'verified_claims': int, 'rejected_claims': int}")
    generated_at: str
