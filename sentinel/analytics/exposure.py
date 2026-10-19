"""4W exposure tagging of the risk-factor section.

Deterministic baseline used by the Graph agent: a transparent taxonomy matches
sentences of the filing's risk-factor section. Every hit keeps the verbatim
sentence and a text-fragment deep link, so nothing here is paraphrased.
"""

from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from typing import Dict, List, Tuple

from sentinel.models import Exposure, ExposureHit, FilingRef, GeoMention
from sentinel.sources.filings import RiskSection, text_fragment_url

# term -> regex. Order inside a category does not matter; each sentence counts once per category.
TAXONOMY: Dict[str, Dict[str, str]] = {
    "watts": {
        "electricity": r"\belectricit(y|ies)\b",
        "power supply": r"\bpower (supply|supplies|availability|shortage|shortages|outage|outages|constraints?)\b",
        "energy cost": r"\benergy (cost|costs|price|prices|supply|supplies|shortage|shortages|consumption)\b",
        "power grid": r"\b(power|electric(al)?) grids?\b|\bgrid (capacity|constraints?|infrastructure|interconnection)\b",
        "natural gas": r"\bnatural gas\b|\bLNG\b",
        "data center power": r"\bdata cent(er|re)s?\b[^.]{0,80}\b(power|energy|electricity)\b",
        "blackout": r"\b(blackouts?|brownouts?|rolling outages?)\b",
    },
    "wires": {
        "copper": r"\bcopper\b",
        "transformer": r"\btransformers?\b",
        "transmission": r"\btransmission (lines?|infrastructure|capacity)\b",
        "aluminum": r"\balumin(i)?um\b",
        "steel": r"\bsteel\b",
        "substrate/packaging": r"\b(substrates?|advanced packaging|CoWoS)\b",
        "cable": r"\bcables?\b",
    },
    "water": {
        "water": r"\bwater\b",
        "drought": r"\bdroughts?\b",
        "flood": r"\bfloods?|flooding\b",
        "typhoon/hurricane": r"\b(typhoons?|hurricanes?|tropical storms?)\b",
        "climate": r"\bclimate change\b|\bextreme weather\b",
    },
    "weaponized": {
        "export control": r"\bexport (controls?|restrictions?|licen[cs]es?|regulations?)\b|\bEAR\b",
        "entity list": r"\bEntity List\b|\bblacklist\w*\b",
        "sanctions": r"\bsanctions?\b|\bembargo(es)?\b",
        "tariff": r"\btariffs?\b|\bduties\b",
        "rare earth": r"\brare[- ]earths?\b",
        "critical minerals": r"\bcritical (minerals?|materials?|raw materials?)\b|\bgallium\b|\bgermanium\b|\bgraphite\b|\blithium\b|\bcobalt\b|\bnickel\b|\bneon\b|\btungsten\b|\bantimony\b",
        "geopolitical conflict": r"\bgeopolitical\b|\b(war|wars|military conflict|armed conflict|hostilities|invasion)\b",
        "national security": r"\bnational security\b",
        "single/sole source": r"\b(single|sole|limited number of) (source|sources|supplier|suppliers)\b|\bsole-source\b|\bsingle-source\b",
    },
}

GEOGRAPHIES: Dict[str, str] = {
    "Taiwan": r"\bTaiwan(ese)?\b",
    "China": r"\bChina\b|\bChinese\b|\bPRC\b|People[’']s Republic of China",
    "Hong Kong": r"\bHong Kong\b",
    "Korea": r"\b(South )?Korea(n)?\b",
    "Japan": r"\bJapan(ese)?\b",
    "Israel": r"\bIsrael(i)?\b",
    "Malaysia": r"\bMalaysia(n)?\b",
    "Vietnam": r"\bVietnam(ese)?\b",
    "India": r"\bIndia(n)?\b",
    "Russia": r"\bRussia(n)?\b",
    "Ukraine": r"\bUkrain(e|ian)\b",
    "Mexico": r"\bMexic(o|an)\b",
    "Netherlands": r"\bNetherlands\b|\bDutch\b",
    "Germany": r"\bGerman(y)?\b",
    "Middle East": r"\bMiddle East\b|\bIsrael-Hamas\b|\bIran\b",
    "Europe": r"\bEurope(an)?\b|\bEU\b",
}

CHOKEPOINTS: Dict[str, str] = {
    "Taiwan Strait": r"\bTaiwan Strait\b|\bcross-strait\b|\bacross the strait\b",
    "Strait of Hormuz": r"\bHormuz\b",
    "Malacca Strait": r"\bMalacca\b",
    "Red Sea / Bab el-Mandeb": r"\bRed Sea\b|\bBab[- ]el[- ]Mandeb\b|\bHouthi",
    "Suez Canal": r"\bSuez\b",
    "Panama Canal": r"\bPanama Canal\b",
    "South China Sea": r"\bSouth China Sea\b",
}

MINERALS: Dict[str, str] = {
    "rare-earths": r"\brare[- ]earths?\b|\bneodymium\b|\bdysprosium\b",
    "gallium": r"\bgallium\b",
    "germanium": r"\bgermanium\b",
    "graphite": r"\bgraphite\b",
    "lithium": r"\blithium\b",
    "cobalt": r"\bcobalt\b",
    "nickel": r"\bnickel\b",
    "copper": r"\bcopper\b",
    "tungsten": r"\btungsten\b",
    "antimony": r"\bantimony\b",
    "magnesium": r"\bmagnesium\b",
    "manganese": r"\bmanganese\b",
    "platinum-group": r"\bplatinum\b|\bpalladium\b",
    "silicon": r"\bpolysilicon\b|\bsilicon metal\b",
    "tin": r"\btin\b(?! the)",
    "aluminum": r"\balumin(i)?um\b|\bbauxite\b",
    "indium": r"\bindium\b",
    "tantalum": r"\btantalum\b",
    "helium": r"\bhelium\b",
}

# Third-party names that matter for physical supply chains (matched case-sensitively).
ENTITY_PATTERN = re.compile(
    r"\b(?:TSMC|Taiwan Semiconductor Manufacturing(?: Company)?(?: Limited)?|Samsung(?: Electronics)?|SK [Hh]ynix|Micron|Hon Hai|Foxconn|"
    r"Amkor|ASE(?: Technology)?|Wistron|Quanta|Pegatron|Intel|GlobalFoundries|SMIC|Semiconductor Manufacturing International|Huawei|"
    r"CATL|Contemporary Amperex|BYD|LG Energy Solution|Panasonic|ASML|Applied Materials|Lam Research|Tokyo Electron|KLA|"
    r"Hynix|Sony|Murata|Infineon|Broadcom|Qualcomm|Apple|Nvidia|NVIDIA|AMD|Advanced Micro Devices)\b"
)

# Calibration constant: hits per 10k characters that yields a score of ~63 (1 - e^-1).
DENSITY_SCALE = {"watts": 0.8, "wires": 0.8, "water": 0.9, "weaponized": 2.2}


def _compile(d: Dict[str, str], flags=re.I) -> Dict[str, re.Pattern]:
    return {k: re.compile(v, flags) for k, v in d.items()}


_TAX = {cat: _compile(terms) for cat, terms in TAXONOMY.items()}
_GEO = _compile(GEOGRAPHIES, flags=0)
_CHK = _compile(CHOKEPOINTS)
_MIN = _compile(MINERALS)


def tag_sentences(section: RiskSection) -> Tuple[Dict[str, List[ExposureHit]], Counter, Dict[str, str]]:
    hits: Dict[str, List[ExposureHit]] = defaultdict(list)
    geo_counts: Counter = Counter()
    geo_sample: Dict[str, str] = {}
    base = section.filing.url
    for sent in section.sentences:
        for cat, terms in _TAX.items():
            for term, rx in terms.items():
                if rx.search(sent):
                    hits[cat].append(
                        ExposureHit(category=cat, term=term, quote=sent, section=section.title, url=text_fragment_url(base, sent))
                    )
                    break  # one hit per category per sentence
        for region, rx in _GEO.items():
            n = len(rx.findall(sent))
            if n:
                geo_counts[region] += n
                if region not in geo_sample or len(sent) < len(geo_sample[region]):
                    geo_sample[region] = sent
    return hits, geo_counts, geo_sample


def _score(cat: str, n_hits: int, section_chars: int) -> float:
    if section_chars <= 0:
        return 0.0
    density = n_hits / (section_chars / 10_000)
    return round(100.0 * (1.0 - math.exp(-density / DENSITY_SCALE[cat])), 1)


def _rank_hits(hits: List[ExposureHit]) -> List[ExposureHit]:
    # Prefer specific, mid-length sentences that mention a geography or a hard constraint.
    def key(h: ExposureHit) -> float:
        s = h.quote
        bonus = 0.0
        if re.search(r"Taiwan|China|Korea|Japan|Hormuz|Malacca|Red Sea", s):
            bonus += 2
        if re.search(r"sole|single|limited number|substantially all|significant portion|depend", s, re.I):
            bonus += 2
        if 120 <= len(s) <= 420:
            bonus += 1
        return bonus

    seen, out = set(), []
    for h in sorted(hits, key=key, reverse=True):
        if h.quote in seen:
            continue
        seen.add(h.quote)
        out.append(h)
    return out


def analyze_exposure(ticker: str, section: RiskSection) -> Exposure:
    hits, geo_counts, geo_sample = tag_sentences(section)
    chars = len(section.text)
    categories = {}
    for cat in TAXONOMY:
        ranked = _rank_hits(hits.get(cat, []))
        term_counts = Counter(h.term for h in hits.get(cat, []))
        categories[cat] = {
            "score": _score(cat, len(hits.get(cat, [])), chars),
            "hits": len(hits.get(cat, [])),
            "terms": dict(term_counts.most_common()),
            "top": [h.model_dump() for h in ranked[:5]],
        }
    full = section.full_text or section.text  # whole annual report: Item 1 names suppliers and raw materials
    geographies = [
        GeoMention(
            region=r,
            mentions=n,
            sample_quote=geo_sample.get(r),
            url=text_fragment_url(section.filing.url, geo_sample[r]) if r in geo_sample else None,
        )
        for r, n in geo_counts.most_common()
    ]
    minerals = [m for m, rx in _MIN.items() if rx.search(full)]
    chokepoints = [c for c, rx in _CHK.items() if rx.search(full)]
    entities = [e for e, _ in Counter(ENTITY_PATTERN.findall(full)).most_common(12)]
    return Exposure(
        ticker=ticker,
        filing=section.filing,
        section_title=section.title,
        section_chars=chars,
        categories=categories,
        geographies=geographies,
        minerals_mentioned=minerals,
        chokepoints_mentioned=chokepoints,
        entities_named=entities,
    )


def filing_ref_label(f: FilingRef) -> str:
    return f"{f.form} filed {f.filed} (accession {f.accession})"
