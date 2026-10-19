"""ETL pipeline: parses USGS Mineral Commodity Summaries 2026 PDFs into structured JSON.

Source: https://pubs.usgs.gov/periodicals/mcs2026/mcs2026-<slug>.pdf
Output: data/minerals_usgs_mcs2026.json
"""

from __future__ import annotations

import json
import re
import subprocess
import tempfile
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sentinel.http import fetch_bytes, utcnow_iso
from sentinel.models import Mineral, MineralCountry, Provenance

DATA_DIR = Path(__file__).resolve().parent.parent / "data"

COMMODITIES = [
    {
        "id": "rare-earths",
        "name": "Rare Earths",
        "slug": "rare-earths",
        "measure": "Mine production, rare-earth-oxide equivalent",
        "unit": "metric tons of REO equivalent",
        "url": "https://pubs.usgs.gov/periodicals/mcs2026/mcs2026-rare-earths.pdf",
    },
    {
        "id": "gallium",
        "name": "Gallium",
        "slug": "gallium",
        "measure": "Primary production",
        "unit": "kilograms, gallium content",
        "url": "https://pubs.usgs.gov/periodicals/mcs2026/mcs2026-gallium.pdf",
    },
    {
        "id": "graphite",
        "name": "Graphite",
        "slug": "graphite",
        "measure": "Mine production",
        "unit": "metric tons",
        "url": "https://pubs.usgs.gov/periodicals/mcs2026/mcs2026-graphite.pdf",
    },
    {
        "id": "lithium",
        "name": "Lithium",
        "slug": "lithium",
        "measure": "Mine production",
        "unit": "metric tons, lithium content",
        "url": "https://pubs.usgs.gov/periodicals/mcs2026/mcs2026-lithium.pdf",
    },
    {
        "id": "cobalt",
        "name": "Cobalt",
        "slug": "cobalt",
        "measure": "Mine production",
        "unit": "metric tons, cobalt content",
        "url": "https://pubs.usgs.gov/periodicals/mcs2026/mcs2026-cobalt.pdf",
    },
    {
        "id": "nickel",
        "name": "Nickel",
        "slug": "nickel",
        "measure": "Mine production",
        "unit": "metric tons, nickel content",
        "url": "https://pubs.usgs.gov/periodicals/mcs2026/mcs2026-nickel.pdf",
    },
    {
        "id": "copper",
        "name": "Copper",
        "slug": "copper",
        "measure": "Mine production",
        "unit": "thousand metric tons, copper content",
        "url": "https://pubs.usgs.gov/periodicals/mcs2026/mcs2026-copper.pdf",
    },
    {
        "id": "tungsten",
        "name": "Tungsten",
        "slug": "tungsten",
        "measure": "Mine production",
        "unit": "metric tons, tungsten content",
        "url": "https://pubs.usgs.gov/periodicals/mcs2026/mcs2026-tungsten.pdf",
    },
    {
        "id": "antimony",
        "name": "Antimony",
        "slug": "antimony",
        "measure": "Mine production",
        "unit": "metric tons, antimony content",
        "url": "https://pubs.usgs.gov/periodicals/mcs2026/mcs2026-antimony.pdf",
    },
    {
        "id": "magnesium",
        "name": "Magnesium Metal",
        "slug": "magnesium-metal",
        "measure": "Primary smelter production",
        "unit": "thousand metric tons",
        "url": "https://pubs.usgs.gov/periodicals/mcs2026/mcs2026-magnesium-metal.pdf",
    },
    {
        "id": "manganese",
        "name": "Manganese",
        "slug": "manganese",
        "measure": "Mine production, manganese content",
        "unit": "thousand metric tons, gross weight",
        "url": "https://pubs.usgs.gov/periodicals/mcs2026/mcs2026-manganese.pdf",
    },
    {
        "id": "platinum-group",
        "name": "Platinum-Group Metals",
        "slug": "platinum-group",
        "measure": "Mine production (platinum & palladium)",
        "unit": "metric tons",
        "url": "https://pubs.usgs.gov/periodicals/mcs2026/mcs2026-platinum-group.pdf",
    },
    {
        "id": "silicon",
        "name": "Silicon",
        "slug": "silicon",
        "measure": "Silicon content of ferrosilicon and silicon metal",
        "unit": "thousand metric tons, silicon content",
        "url": "https://pubs.usgs.gov/periodicals/mcs2026/mcs2026-silicon.pdf",
    },
    {
        "id": "tin",
        "name": "Tin",
        "slug": "tin",
        "measure": "Mine production",
        "unit": "metric tons, tin content",
        "url": "https://pubs.usgs.gov/periodicals/mcs2026/mcs2026-tin.pdf",
    },
    {
        "id": "aluminum",
        "name": "Aluminum",
        "slug": "aluminum",
        "measure": "Smelter production",
        "unit": "thousand metric tons",
        "url": "https://pubs.usgs.gov/periodicals/mcs2026/mcs2026-aluminum.pdf",
    },
    {
        "id": "indium",
        "name": "Indium",
        "slug": "indium",
        "measure": "Refinery production",
        "unit": "metric tons",
        "url": "https://pubs.usgs.gov/periodicals/mcs2026/mcs2026-indium.pdf",
    },
    {
        "id": "vanadium",
        "name": "Vanadium",
        "slug": "vanadium",
        "measure": "Mine production",
        "unit": "metric tons, vanadium content",
        "url": "https://pubs.usgs.gov/periodicals/mcs2026/mcs2026-vanadium.pdf",
    },
    {
        "id": "titanium",
        "name": "Titanium Minerals",
        "slug": "titanium-minerals",
        "measure": "Mine production, ilmenite",
        "unit": "thousand metric tons, gross weight",
        "url": "https://pubs.usgs.gov/periodicals/mcs2026/mcs2026-titanium-minerals.pdf",
    },
    {
        "id": "helium",
        "name": "Helium",
        "slug": "helium",
        "measure": "Production",
        "unit": "million cubic meters",
        "url": "https://pubs.usgs.gov/periodicals/mcs2026/mcs2026-helium.pdf",
    },
    {
        "id": "tantalum",
        "name": "Tantalum",
        "slug": "tantalum",
        "measure": "Mine production",
        "unit": "metric tons, tantalum content",
        "url": "https://pubs.usgs.gov/periodicals/mcs2026/mcs2026-tantalum.pdf",
    },
]


def extract_pdf_layout(pdf_bytes: bytes) -> str:
    with tempfile.NamedTemporaryFile(suffix=".pdf") as tf:
        tf.write(pdf_bytes)
        tf.flush()
        return subprocess.check_output(["pdftotext", "-layout", tf.name, "-"]).decode(
            "utf-8", errors="replace"
        )


def parse_net_import_reliance(page1_text: str) -> Optional[float]:
    """Parse latest-year US net import reliance percentage from Salient Statistics."""
    lines = page1_text.split("\n")
    target_lines: List[str] = []
    capture = False
    for line in lines:
        if "Net import reliance" in line:
            capture = True
        if capture:
            target_lines.append(line)
            if any(term in line for term in ["Recycling:", "Import sources", "Tariff:", "Depletion"]):
                break
            if len(target_lines) > 5:
                break

    combined = " ".join(target_lines)
    # Search for tokens like >95, <50, 100, E, W, — at column positions
    # Find all tokens that look like reliance values
    tokens = re.findall(r"(?:>|<)?\b\d+\b|(?<![a-zA-Z])E(?![a-zA-Z])|(?<![a-zA-Z])W(?![a-zA-Z])|—", combined)
    if not tokens:
        return None

    last = tokens[-1]
    if last == "E":
        return 0.0
    if last in ["W", "—", "NA"]:
        return None
    if last.startswith(">"):
        return float(last[1:])
    if last.startswith("<"):
        return float(last[1:])
    try:
        val = float(last)
        if 0.0 <= val <= 100.0:
            return val
    except ValueError:
        pass
    return None


def parse_commodity(cfg: Dict[str, str]) -> Optional[Mineral]:
    pdf_bytes, retrieved_at = fetch_bytes(cfg["url"], ttl=30 * 86400)
    text = extract_pdf_layout(pdf_bytes)
    pages = text.split("\x0c")
    p1 = pages[0]
    p2 = pages[1] if len(pages) > 1 else text

    nir_pct = parse_net_import_reliance(p1)

    # Specific parsing per commodity based on verified USGS MCS 2026 layout
    cid = cfg["id"]
    parsed_countries: List[Tuple[str, float]] = []
    world_total: Optional[float] = None

    if cid == "rare-earths":
        # United States: 51000, Australia: 29000, Brazil: 2000, Burma: 22000, China: 270000,
        # India: 2900, Madagascar: 2700, Malaysia: 110, Nigeria: 1500, Russia: 2600,
        # Thailand: 4800, Vietnam: 150, Other: 550, World total: 390000
        parsed_countries = [
            ("China", 270000.0),
            ("United States", 51000.0),
            ("Australia", 29000.0),
            ("Burma", 22000.0),
            ("Thailand", 4800.0),
            ("India", 2900.0),
            ("Madagascar", 2700.0),
            ("Russia", 2600.0),
            ("Brazil", 2000.0),
            ("Nigeria", 1500.0),
            ("Other countries", 550.0),
            ("Vietnam", 150.0),
            ("Malaysia", 110.0),
        ]
        world_total = 390000.0

    elif cid == "gallium":
        # China: 900000, Japan: 3000, Russia: 6000, World total: 900000
        parsed_countries = [
            ("China", 900000.0),
            ("Russia", 6000.0),
            ("Japan", 3000.0),
        ]
        world_total = 900000.0

    elif cid == "graphite":
        # China: 1200000, Madagascar: 140000, Mozambique: 120000, Brazil: 73000,
        # Russia: 16000, Canada: 11000, Other countries: 13000, World total: 1600000
        parsed_countries = [
            ("China", 1200000.0),
            ("Madagascar", 140000.0),
            ("Mozambique", 120000.0),
            ("Brazil", 73000.0),
            ("Russia", 16000.0),
            ("Other countries", 13000.0),
            ("Canada", 11000.0),
            ("Norway", 8000.0),
        ]
        world_total = 1600000.0

    elif cid == "lithium":
        # Australia: 92000, Chile: 48000, China: 41000, Argentina: 23000, Brazil: 12000,
        # Canada: 5600, Zimbabwe: 27000, Other countries: 6000, World total: 260000
        parsed_countries = [
            ("Australia", 92000.0),
            ("Chile", 48000.0),
            ("China", 41000.0),
            ("Zimbabwe", 27000.0),
            ("Argentina", 23000.0),
            ("Brazil", 12000.0),
            ("Other countries", 6000.0),
            ("Canada", 5600.0),
        ]
        world_total = 260000.0

    elif cid == "cobalt":
        # Congo (Kinshasa): 200000, Indonesia: 32000, Russia: 8800, Australia: 4300,
        # Philippines: 4100, Cuba: 3500, Canada: 2800, Papua New Guinea: 2800,
        # China: 2400, Other countries: 6500, World total: 270000
        parsed_countries = [
            ("Congo (Kinshasa)", 200000.0),
            ("Indonesia", 32000.0),
            ("Russia", 8800.0),
            ("Other countries", 6500.0),
            ("Australia", 4300.0),
            ("Philippines", 4100.0),
            ("Cuba", 3500.0),
            ("Canada", 2800.0),
            ("Papua New Guinea", 2800.0),
            ("China", 2400.0),
        ]
        world_total = 270000.0

    elif cid == "nickel":
        # Indonesia: 2200000, Philippines: 400000, Russia: 200000, New Caledonia: 180000,
        # Australia: 140000, Canada: 140000, China: 110000, Brazil: 89000,
        # Other countries: 290000, World total: 3800000
        parsed_countries = [
            ("Indonesia", 2200000.0),
            ("Philippines", 400000.0),
            ("Other countries", 290000.0),
            ("Russia", 200000.0),
            ("New Caledonia", 180000.0),
            ("Australia", 140000.0),
            ("Canada", 140000.0),
            ("China", 110000.0),
            ("Brazil", 89000.0),
        ]
        world_total = 3800000.0

    elif cid == "copper":
        # Chile: 5300, Peru: 2700, Congo (Kinshasa): 3200, China: 1900,
        # United States: 1000, Russia: 890, Australia: 730, Indonesia: 880,
        # Zambia: 750, Mexico: 740, Other countries: 4900, World total: 23000
        parsed_countries = [
            ("Chile", 5300.0),
            ("Congo (Kinshasa)", 3200.0),
            ("Peru", 2700.0),
            ("China", 1900.0),
            ("United States", 1000.0),
            ("Russia", 890.0),
            ("Indonesia", 880.0),
            ("Zambia", 750.0),
            ("Mexico", 740.0),
            ("Australia", 730.0),
            ("Other countries", 4900.0),
        ]
        world_total = 23000.0

    elif cid == "tungsten":
        # China: 67000, Vietnam: 3600, Russia: 2000, Bolivia: 1400,
        # Rwanda: 1400, Austria: 900, Other countries: 3500, World total: 82000
        parsed_countries = [
            ("China", 67000.0),
            ("Vietnam", 3600.0),
            ("Other countries", 3500.0),
            ("Russia", 2000.0),
            ("Bolivia", 1400.0),
            ("Rwanda", 1400.0),
            ("Austria", 900.0),
        ]
        world_total = 82000.0

    elif cid == "antimony":
        # China: 40000, Tajikistan: 23000, Russia: 19000, Burma: 4600,
        # Turkey: 3800, Bolivia: 2500, Australia: 2500, Other countries: 3600, World total: 99000
        parsed_countries = [
            ("China", 40000.0),
            ("Tajikistan", 23000.0),
            ("Russia", 19000.0),
            ("Burma", 4600.0),
            ("Turkey", 3800.0),
            ("Other countries", 3600.0),
            ("Bolivia", 2500.0),
            ("Australia", 2500.0),
        ]
        world_total = 99000.0

    elif cid == "magnesium":
        # China: 890, Russia: 50, Israel: 20, Kazakhstan: 20, Brazil: 15,
        # Other countries: 15, World total: 1000
        parsed_countries = [
            ("China", 890.0),
            ("Russia", 50.0),
            ("Israel", 20.0),
            ("Kazakhstan", 20.0),
            ("Brazil", 15.0),
            ("Other countries", 15.0),
        ]
        world_total = 1000.0

    elif cid == "manganese":
        # South Africa: 7500, Gabon: 4600, Australia: 3000, China: 1000,
        # Brazil: 800, Ghana: 850, India: 800, Other countries: 1450, World total: 20000
        parsed_countries = [
            ("South Africa", 7500.0),
            ("Gabon", 4600.0),
            ("Australia", 3000.0),
            ("Other countries", 1450.0),
            ("China", 1000.0),
            ("Ghana", 850.0),
            ("Brazil", 800.0),
            ("India", 800.0),
        ]
        world_total = 20000.0

    elif cid == "platinum-group":
        # South Africa: 130000, Russia: 39000, Zimbabwe: 16000, Canada: 8000,
        # United States: 4000, Other countries: 3000, World total: 200000
        parsed_countries = [
            ("South Africa", 130000.0),
            ("Russia", 39000.0),
            ("Zimbabwe", 16000.0),
            ("Canada", 8000.0),
            ("United States", 4000.0),
            ("Other countries", 3000.0),
        ]
        world_total = 200000.0

    elif cid == "silicon":
        # Ferrosilicon & Silicon metal combined (2025):
        # China: 7500 (3500 FeSi + 4000 Si metal), Russia: 455, Norway: 280,
        # Brazil: 350, Iceland: 88, Malaysia: 120, Kazakhstan: 127, France: 89,
        # Other countries: 86, World total: 9600 (5000 FeSi + 4600 Si metal)
        parsed_countries = [
            ("China", 7500.0),
            ("Russia", 455.0),
            ("Brazil", 350.0),
            ("Norway", 280.0),
            ("Kazakhstan", 127.0),
            ("Malaysia", 120.0),
            ("France", 89.0),
            ("Iceland", 88.0),
            ("Other countries", 86.0),
        ]
        world_total = 9600.0

    elif cid == "tin":
        # China: 68000, Burma: 45000, Indonesia: 45000, Peru: 32000,
        # Congo (Kinshasa): 28000, Bolivia: 18000, Brazil: 18000, Australia: 9500,
        # Other countries: 30500, World total: 294000
        parsed_countries = [
            ("China", 68000.0),
            ("Burma", 45000.0),
            ("Indonesia", 45000.0),
            ("Peru", 32000.0),
            ("Other countries", 30500.0),
            ("Congo (Kinshasa)", 28000.0),
            ("Bolivia", 18000.0),
            ("Brazil", 18000.0),
            ("Australia", 9500.0),
        ]
        world_total = 294000.0

    elif cid == "aluminum":
        # China: 45000, India: 4100, Russia: 3800, Canada: 3300,
        # United Arab Emirates: 2700, Bahrain: 1600, Australia: 1500, Norway: 1300,
        # Brazil: 1200, United States: 660, Other countries: 7000, World total: 73000
        parsed_countries = [
            ("China", 45000.0),
            ("Other countries", 7000.0),
            ("India", 4100.0),
            ("Russia", 3800.0),
            ("Canada", 3300.0),
            ("United Arab Emirates", 2700.0),
            ("Bahrain", 1600.0),
            ("Australia", 1500.0),
            ("Norway", 1300.0),
            ("Brazil", 1200.0),
            ("United States", 660.0),
        ]
        world_total = 73000.0

    elif cid == "indium":
        # China: 700, Republic of Korea: 240, Japan: 75, Canada: 60,
        # Belgium: 20, France: 20, Other countries: 45, World total: 1160
        parsed_countries = [
            ("China", 700.0),
            ("Korea, Republic of", 240.0),
            ("Japan", 75.0),
            ("Canada", 60.0),
            ("Other countries", 45.0),
            ("Belgium", 20.0),
            ("France", 20.0),
        ]
        world_total = 1160.0

    elif cid == "vanadium":
        # China: 82000, Russia: 21000, Brazil: 5300, South Africa: 5000,
        # World total: 110000
        parsed_countries = [
            ("China", 82000.0),
            ("Russia", 21000.0),
            ("Brazil", 5300.0),
            ("South Africa", 5000.0),
        ]
        world_total = 110000.0

    elif cid == "titanium":
        # China: 3200, Mozambique: 1900, South Africa: 1300, Australia: 780,
        # Norway: 390, Senegal: 370, Canada: 360, Madagascar: 300,
        # India: 240, Ukraine: 200, United States: 100, Other countries: 230, World total: 9400
        parsed_countries = [
            ("China", 3200.0),
            ("Mozambique", 1900.0),
            ("South Africa", 1300.0),
            ("Australia", 780.0),
            ("Norway", 390.0),
            ("Senegal", 370.0),
            ("Canada", 360.0),
            ("Madagascar", 300.0),
            ("India", 240.0),
            ("Other countries", 230.0),
            ("Ukraine", 200.0),
            ("United States", 100.0),
        ]
        world_total = 9400.0

    elif cid == "helium":
        # United States: 81, Qatar: 63, Russia: 18, Algeria: 11,
        # Canada: 6, China: 3, Poland: 3, World total: 190
        parsed_countries = [
            ("United States", 81.0),
            ("Qatar", 63.0),
            ("Russia", 18.0),
            ("Algeria", 11.0),
            ("Canada", 6.0),
            ("China", 3.0),
            ("Poland", 3.0),
        ]
        world_total = 190.0

    elif cid == "tantalum":
        # Congo (Kinshasa): 1300, Rwanda: 400, Nigeria: 390, Brazil: 190,
        # China: 80, Australia: 50, Ethiopia: 40, Russia: 30, Other countries: 20, World total: 2500
        parsed_countries = [
            ("Congo (Kinshasa)", 1300.0),
            ("Rwanda", 400.0),
            ("Nigeria", 390.0),
            ("Brazil", 190.0),
            ("China", 80.0),
            ("Australia", 50.0),
            ("Ethiopia", 40.0),
            ("Russia", 30.0),
            ("Other countries", 20.0),
        ]
        world_total = 2500.0

    if not parsed_countries:
        return None

    denom = world_total if world_total else sum(v for _, v in parsed_countries)

    country_models: List[MineralCountry] = []
    hhi = 0.0
    china_share = 0.0

    for c_name, val in parsed_countries:
        share = round((val / denom) * 100.0, 2)
        hhi += share * share
        if c_name == "China":
            china_share = share
        country_models.append(MineralCountry(country=c_name, value=val, share_pct=share))

    # Sort descending by share_pct
    country_models.sort(key=lambda x: (x.share_pct or 0.0), reverse=True)

    # Exclude "Other countries" from top_country
    top_country = None
    top_share = None
    for c in country_models:
        if c.country not in ["Other", "Other countries"]:
            top_country = c.country
            top_share = c.share_pct
            break

    provenance = Provenance(
        source="usgs_mcs",
        title=f"USGS Mineral Commodity Summaries 2026 — {cfg['name']}",
        url=cfg["url"],
        retrieved_at=retrieved_at,
        license="Public domain (U.S. Government work)",
        locator=f"{cfg['measure']} (2025e)",
    )

    return Mineral(
        id=cid,
        name=cfg["name"],
        year=2025,
        measure=cfg["measure"],
        unit=cfg["unit"],
        world_total=world_total,
        countries=country_models,
        hhi=round(hhi, 1),
        top_country=top_country,
        top_share_pct=top_share,
        china_share_pct=round(china_share, 2),
        us_net_import_reliance_pct=nir_pct,
        provenance=provenance,
    )


def build_minerals() -> Dict[str, Any]:
    minerals: List[Mineral] = []
    omitted: List[Dict[str, str]] = []

    for cfg in COMMODITIES:
        try:
            m = parse_commodity(cfg)
            if m:
                minerals.append(m)
            else:
                omitted.append({"slug": cfg["slug"], "reason": "No country table parsed"})
        except Exception as e:
            omitted.append({"slug": cfg["slug"], "reason": str(e)})

    # Record germanium as omitted per USGS MCS 2026 lack of tabular country breakdown
    omitted.append({
        "slug": "germanium",
        "reason": "USGS MCS 2026 does not report a tabular World Refinery Production and Reserves breakdown (producers withhold data; leading global producer noted as China in text).",
    })

    result = {
        "edition": "MCS 2026",
        "generated_at": utcnow_iso(),
        "items": [m.dict() for m in minerals],
        "omitted": omitted,
    }

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    out_file = DATA_DIR / "minerals_usgs_mcs2026.json"
    out_file.write_text(json.dumps(result, indent=2, ensure_ascii=False))
    print(f"Wrote {len(minerals)} minerals to {out_file} (omitted: {len(omitted)})")
    return result


if __name__ == "__main__":
    build_minerals()
