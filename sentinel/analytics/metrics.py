"""Sovereign risk metrics: CPI, GPI, SRS, FAR.

Each metric is a weighted sum of components in [0, 100]; every component
carries the ledger ids of the evidence it was computed from. Formulas and
weights are documented in docs/METHODOLOGY.md (methodology v1).
"""

from __future__ import annotations

import math
from typing import Dict, List, Optional, Tuple

from sentinel.ledger import Ledger
from sentinel.models import Chokepoint, CompanyProfile, Exposure, Metric, MetricComponent, Mineral, SanctionsScreen, StressResult

TAIWAN_SUPPLIERS = ("TSMC", "Taiwan Semiconductor", "Hon Hai", "Foxconn", "ASE", "Wistron", "Quanta", "Pegatron")
ASIA = ("China", "Hong Kong", "Taiwan", "Korea", "Japan", "Malaysia", "Vietnam", "India")
FOREIGN_HQ_TAIWAN = ("TAIWAN",)

# Chokepoints that carry each region's seaborne trade (PortWatch portid).
REGION_ROUTES: Dict[str, List[str]] = {
    "Taiwan": ["chokepoint11", "chokepoint14"],          # Taiwan Strait, Luzon Strait
    "China": ["chokepoint11", "chokepoint5"],            # Taiwan Strait, Malacca
    "Hong Kong": ["chokepoint11"],
    "Korea": ["chokepoint12", "chokepoint11"],           # Korea Strait, Taiwan Strait
    "Japan": ["chokepoint12", "chokepoint14"],
    "Malaysia": ["chokepoint5"],
    "Vietnam": ["chokepoint5"],
    "India": ["chokepoint5", "chokepoint4"],             # Malacca, Bab el-Mandeb
    "Middle East": ["chokepoint6", "chokepoint4"],       # Hormuz, Bab el-Mandeb
    "Israel": ["chokepoint1", "chokepoint4"],            # Suez, Bab el-Mandeb
    "Europe": ["chokepoint1", "chokepoint4"],
    "Germany": ["chokepoint1", "chokepoint4"],
    "Netherlands": ["chokepoint1", "chokepoint4"],
    "Mexico": ["chokepoint2"],                           # Panama
}

# Minerals a sector depends on even when its filing does not name them (SIC prefix -> USGS ids).
SECTOR_MINERALS: Dict[str, List[str]] = {
    "367": ["gallium", "germanium", "silicon", "helium", "tantalum"],     # electronic components & semiconductors
    "357": ["rare-earths", "tantalum", "tin", "gallium"],                 # computers
    "366": ["rare-earths", "gallium", "germanium", "tantalum"],           # communications equipment
    "371": ["lithium", "cobalt", "nickel", "graphite", "rare-earths"],    # motor vehicles
    "355": ["rare-earths", "tungsten", "copper"],                         # special industry machinery
    "372": ["titanium", "rare-earths", "cobalt"],                         # aircraft
    "376": ["rare-earths", "gallium", "germanium", "titanium"],           # guided missiles & space
    "362": ["copper", "rare-earths", "aluminum"],                         # electrical industrial apparatus
    "491": ["copper", "aluminum"],                                        # electric services
    "131": ["nickel", "manganese"],                                       # oil & gas
}

FAR_BANDS = [(85, "AAA"), (75, "AA"), (65, "A"), (55, "BBB"), (45, "BB"), (35, "B"), (0, "CCC")]


def clip(x: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, x))


def _sat(x: float, scale: float) -> float:
    """Saturating map [0, inf) -> [0, 1)."""
    return 1.0 - math.exp(-max(0.0, x) / scale)


def geo_counts(exposure: Exposure) -> Dict[str, int]:
    return {g.region: g.mentions for g in exposure.geographies}


def channel_exposures(profile: CompanyProfile, exposure: Exposure, minerals: List[Mineral]) -> Dict[str, float]:
    """Exposure in [0,1] for each scenario channel, from filing evidence."""
    g = geo_counts(exposure)
    hq = (profile.hq_country or "").upper()
    tw_entities = sum(1 for e in exposure.entities_named if any(e.startswith(s) for s in TAIWAN_SUPPLIERS))
    strait = 1 if "Taiwan Strait" in exposure.chokepoints_mentioned else 0
    taiwan = 1.0 if hq in FOREIGN_HQ_TAIWAN else _sat(g.get("Taiwan", 0) + 2 * tw_entities + 3 * strait, 8.0)

    china_dominant = [m for m in minerals if (m.china_share_pct or 0) >= 50]
    weap_hits = exposure.categories.get("weaponized", {}).get("hits", 0)
    china = _sat(0.5 * (g.get("China", 0) + g.get("Hong Kong", 0)) + 6 * len(china_dominant) + 0.2 * weap_hits, 10.0)

    watts_hits = exposure.categories.get("watts", {}).get("hits", 0)
    hormuz_m = 1 if "Strait of Hormuz" in exposure.chokepoints_mentioned else 0
    hormuz = _sat(2 * g.get("Middle East", 0) + 0.5 * watts_hits + 5 * hormuz_m, 8.0)

    asia = sum(g.get(r, 0) for r in ASIA)
    malacca_m = sum(1 for c in ("Malacca Strait", "South China Sea") if c in exposure.chokepoints_mentioned)
    malacca = _sat(0.15 * asia + 5 * malacca_m, 12.0)
    return {"taiwan": round(taiwan, 4), "china_export": round(china, 4), "hormuz": round(hormuz, 4), "malacca": round(malacca, 4)}


def relevant_minerals(profile: CompanyProfile, exposure: Exposure, all_minerals: List[Mineral]) -> List[Tuple[Mineral, str]]:
    """(mineral, 'filing' | 'sector') — minerals named in the filing first, then sector-implied."""
    by_id = {m.id: m for m in all_minerals}
    out: List[Tuple[Mineral, str]] = []
    for mid in exposure.minerals_mentioned:
        if mid in by_id:
            out.append((by_id[mid], "filing"))
    sic = (profile.sic or "")[:3]
    for mid in SECTOR_MINERALS.get(sic, []):
        if mid in by_id and mid not in exposure.minerals_mentioned:
            out.append((by_id[mid], "sector"))
    return out


def relevant_routes(exposure: Exposure) -> Dict[str, float]:
    """portid -> weight (sums to 1) from the filing's geography mentions."""
    weights: Dict[str, float] = {}
    for gm in exposure.geographies:
        for pid in REGION_ROUTES.get(gm.region, []):
            weights[pid] = weights.get(pid, 0.0) + gm.mentions
    total = sum(weights.values())
    return {k: v / total for k, v in weights.items()} if total else {}


def compute_metrics(
    profile: CompanyProfile,
    exposure: Exposure,
    minerals: List[Tuple[Mineral, str]],
    chokepoints: List[Chokepoint],
    sanctions: List[SanctionsScreen],
    stress: StressResult,
    ledger: Ledger,
    ev: Dict[str, str],
) -> List[Metric]:
    """`ev` maps evidence keys (e.g. 'exp:watts', 'min:gallium', 'chk:chokepoint11', 'xbrl:revenue') to ledger ids."""
    cat = {c: exposure.categories.get(c, {}).get("score", 0.0) for c in ("watts", "wires", "water", "weaponized")}
    ids = lambda *keys: [ev[k] for k in keys if k in ev]  # noqa: E731

    # ---- CPI -------------------------------------------------------------
    v4w = 0.30 * cat["watts"] + 0.20 * cat["wires"] + 0.15 * cat["water"] + 0.35 * cat["weaponized"]
    if minerals:
        weighted = [(m.hhi or 0) / 100.0 * (1.0 if src == "filing" else 0.6) for m, src in minerals]
        top = sorted(weighted, reverse=True)[:3]
        mineral_conc = sum(top) / len(top)
    else:
        mineral_conc = 0.0
    route_w = relevant_routes(exposure)
    by_id = {c.id: c for c in chokepoints}
    route_stress = 0.0
    for pid, w in route_w.items():
        c = by_id.get(pid)
        drop = clip(-(c.delta_pct or 0.0) / 30.0, 0.0, 1.0) if c else 0.0
        route_stress += w * (0.6 + 0.4 * drop)
    asia_share = sum(gm.mentions for gm in exposure.geographies if gm.region in ASIA) / max(1, sum(gm.mentions for gm in exposure.geographies))
    route_stress = 100.0 * route_stress * (0.5 + 0.5 * asia_share) if route_w else 0.0
    cpi_components = [
        MetricComponent(name="4W physical vulnerability (V4W)", value=round(v4w, 1), weight=0.40, evidence_ids=ids("exp:watts", "exp:wires", "exp:water", "exp:weaponized"),
                        note="0.30·Watts + 0.20·Wires + 0.15·Water + 0.35·Weaponized, from risk-factor sentence density"),
        MetricComponent(name="Critical-mineral concentration (USGS HHI)", value=round(mineral_conc, 1), weight=0.35,
                        evidence_ids=[ev[f"min:{m.id}"] for m, _ in minerals if f"min:{m.id}" in ev],
                        note="mean of top-3 country HHI/100 of relevant minerals (sector-implied minerals ×0.6)"),
        MetricComponent(name="Maritime route stress (IMF PortWatch)", value=round(route_stress, 1), weight=0.25,
                        evidence_ids=[ev[f"chk:{p}"] for p in route_w if f"chk:{p}" in ev],
                        note="route-weighted (0.6 structural + 0.4 × current transit drop vs 365d baseline) × Asia share"),
    ]
    cpi = clip(sum(c.value * c.weight for c in cpi_components))

    # ---- GPI -------------------------------------------------------------
    terms = exposure.categories.get("weaponized", {}).get("terms", {})
    ctrl_hits = sum(terms.get(t, 0) for t in ("export control", "entity list", "sanctions", "national security"))
    tariff_hits = terms.get("tariff", 0)
    per10k = max(1.0, exposure.section_chars / 10_000)
    export_ctrl = 100 * _sat(ctrl_hits / per10k, 1.2)
    tariff = 100 * _sat(tariff_hits / per10k, 0.8)
    g = geo_counts(exposure)
    total_geo = max(1, sum(g.values()))
    ct_share = 100.0 * (g.get("China", 0) + g.get("Hong Kong", 0) + g.get("Taiwan", 0)) / total_geo
    sanc_hits = sum(1 for s in sanctions if s.matches)
    sanc = clip(35.0 * sanc_hits)
    gpi_components = [
        MetricComponent(name="Export-control & sanctions language", value=round(export_ctrl, 1), weight=0.35, evidence_ids=ids("exp:weaponized"),
                        note="density of export-control / Entity List / sanctions / national-security sentences"),
        MetricComponent(name="China + Taiwan share of geographic risk mentions", value=round(ct_share, 1), weight=0.25, evidence_ids=ids("geo:China", "geo:Taiwan"),
                        note="share of all country/region mentions in the risk section"),
        MetricComponent(name="Tariff exposure language", value=round(tariff, 1), weight=0.20, evidence_ids=ids("exp:weaponized")),
        MetricComponent(name="Companies named in the filing that appear on US screening lists", value=round(sanc, 1), weight=0.20,
                        evidence_ids=[ev[f"sanc:{s.query}"] for s in sanctions if f"sanc:{s.query}" in ev],
                        note="35 points per named counterparty with a Consolidated Screening List match"),
    ]
    gpi = clip(sum(c.value * c.weight for c in gpi_components))

    # ---- SRS -------------------------------------------------------------
    p_dd = stress.prob_fcf_drawdown_gt_20 * 100.0
    rev_tail = clip(abs(stress.revenue_shock_pct.get("p05", 0.0)) / 30.0 * 100.0)
    ppe = (profile.fundamentals.get("ppe_net") or [None])[-1]
    assets = (profile.fundamentals.get("total_assets") or [None])[-1]
    asset_int = clip(100.0 * ppe.value / assets.value * 2.0) if ppe and assets and assets.value else 0.0
    srs_components = [
        MetricComponent(name="P(FCF drawdown > 20%) under scenario", value=round(p_dd, 1), weight=0.50, evidence_ids=ids("stress:p_dd"),
                        note="Monte Carlo, sandbox-executed"),
        MetricComponent(name="Tail revenue shock (p05)", value=round(rev_tail, 1), weight=0.30, evidence_ids=ids("stress:rev_p05"),
                        note="|p05 revenue shock| / 30% × 100"),
        MetricComponent(name="Fixed-asset intensity (PP&E / assets × 2)", value=round(asset_int, 1), weight=0.20, evidence_ids=ids("xbrl:ppe_net", "xbrl:total_assets"),
                        note="assets that cannot be moved if a region is cut off"),
    ]
    srs = clip(sum(c.value * c.weight for c in srs_components))

    # ---- FAR -------------------------------------------------------------
    rev = (profile.fundamentals.get("revenue") or [None])[-1]
    cash = (profile.fundamentals.get("cash") or [None])[-1]
    cfo = (profile.fundamentals.get("cfo") or [None])[-1]
    capex = (profile.fundamentals.get("capex") or [None])[-1]
    resilience = 50.0
    if rev and rev.value:
        if cash:
            resilience += 25.0 * _sat(cash.value / rev.value, 0.25)
        if cfo and capex:
            fcf_margin = (cfo.value - capex.value) / rev.value
            resilience += 25.0 * clip(fcf_margin / 0.25, -1.0, 1.0)
    resilience = clip(resilience)
    far_components = [
        MetricComponent(name="Low geopolitical vulnerability (100 − GPI)", value=round(100 - gpi, 1), weight=0.45, evidence_ids=[]),
        MetricComponent(name="Low chokepoint paralysis (100 − CPI)", value=round(100 - cpi, 1), weight=0.30, evidence_ids=[]),
        MetricComponent(name="Financial resilience (cash buffer, FCF margin)", value=round(resilience, 1), weight=0.25, evidence_ids=ids("xbrl:cash", "xbrl:cfo", "xbrl:capex", "xbrl:revenue"),
                        note="50 + 25·sat(cash/revenue) + 25·clip(FCF margin/25%)"),
    ]
    far = clip(sum(c.value * c.weight for c in far_components))
    rating = next(r for t, r in FAR_BANDS if far >= t)

    return [
        Metric(code="CPI", name="Chokepoint Paralysis Index", value=round(cpi, 1), formula="CPI = 0.40·V4W + 0.35·MineralHHI + 0.25·RouteStress", components=cpi_components),
        Metric(code="GPI", name="Geopolitical Vulnerability Index", value=round(gpi, 1), formula="GPI = 0.35·ExportCtrl + 0.25·ChinaTaiwanShare + 0.20·Tariff + 0.20·ScreeningHits", components=gpi_components),
        Metric(code="SRS", name="Stranded Risk Score", value=round(srs, 1), formula="SRS = 0.50·P(FCF↓>20%) + 0.30·TailRevenueShock + 0.20·AssetIntensity", components=srs_components),
        Metric(code="FAR", name="Friend-Shoring Alpha Rating", value=round(far, 1), rating=rating, formula="FAR = 0.45·(100−GPI) + 0.30·(100−CPI) + 0.25·Resilience → AAA…CCC", components=far_components),
    ]


def latest(profile: CompanyProfile, concept: str) -> Optional[float]:
    s = profile.fundamentals.get(concept) or []
    return s[-1].value if s else None
