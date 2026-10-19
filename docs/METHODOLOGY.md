# Methodology v1

Geo-Alpha Sentinel turns public primary sources into four sovereign-risk metrics and a verified investment-committee memo.
Everything here is code in this repository; every intermediate value is an `Evidence` record with provenance (see `sentinel/models.py`).

## 1. Evidence ledger

| Evidence | Source | Code |
|---|---|---|
| Fundamentals (revenue, gross profit, operating income, CFO, capex, cash, PP&E, assets, R&D) — latest fiscal year, one value per year from annual filings, restatements win | SEC XBRL company facts | `sentinel/sources/sec.py` |
| Risk-factor sentences (10-K Item 1A / 20-F Item 3.D), verbatim, each with a W3C text-fragment link into the filing | SEC EDGAR primary document | `sentinel/sources/filings.py` |
| Country concentration of mineral production (HHI), China share, US net import reliance | USGS Mineral Commodity Summaries 2026 | `etl/build_minerals.py` |
| Daily ship transits for 28 maritime chokepoints, 7-day mean vs prior 365-day baseline | IMF PortWatch (AIS) | `sentinel/sources/portwatch.py` |
| Screening matches for companies named in the filing | U.S. Trade.gov Consolidated Screening List | `sentinel/sources/sanctions.py` |
| US import partner concentration for sector HS codes | UN Comtrade | `sentinel/sources/comtrade.py` |
| Derived numbers (margins, FCF, growth) and all stress statistics | deterministic sandbox | `sentinel/sandbox.py` |

## 2. 4W exposure scores (0–100)

The risk-factor section is split into sentences. A transparent taxonomy (`sentinel/analytics/exposure.py`) tags each sentence at most once per category:

* **Watts** — electricity, power supply/outage, energy cost, power grid, natural gas/LNG, data-centre power, blackouts
* **Wires** — copper, transformers, transmission, aluminium, steel, substrates/advanced packaging, cables
* **Water** — water, drought, flood, typhoon/hurricane, climate change/extreme weather
* **Weaponized** — export controls, Entity List, sanctions/embargo, tariffs, rare earths, critical minerals, geopolitical conflict, national security, single/sole source

`score_c = 100 · (1 − exp(−d_c / s_c))`, where `d_c` is tagged sentences per 10,000 characters of the section and `s_c` is a category scale (Watts 0.8, Wires 0.8, Water 0.9, Weaponized 2.2) so that a typical heavy discloser scores ≈ 63.

## 3. Scenario channel exposures (0–1)

| Channel | Formula (saturating `sat(x, k) = 1 − e^{−x/k}`) |
|---|---|
| Taiwan | 1 if headquartered in Taiwan, else `sat(TaiwanMentions + 2·TaiwanSuppliersNamed + 3·[Taiwan Strait mentioned], 8)` |
| China export controls | `sat(0.5·(China+HK mentions) + 6·#minerals with China share ≥ 50% + 0.2·Weaponized sentences, 10)` |
| Hormuz | `sat(2·MiddleEast mentions + 0.5·Watts sentences + 5·[Hormuz mentioned], 8)` |
| Malacca | `sat(0.15·Asia mentions + 5·#[Malacca, South China Sea mentioned], 12)` |

Suppliers and raw materials are read from the whole annual report (Item 1 names them); geography mentions come from the risk-factor section.

## 4. Stress test (Monte Carlo, sandbox-executed)

Program text: `sentinel/analytics/stress.py` (`STRESS_PROGRAM`), seeded (default 42), 20,000 paths. For each channel *k* with slider severity `s_k` and exposure `e_k`:

* event probability by the horizon `p_k = min(0.95, s_k · (0.55 + 0.10 · years))`
* revenue depth while disrupted `Beta(2,3) · L_k · e_k · (0.5 + 0.5 s_k)` with caps `L = {Taiwan 0.65, China 0.30, Hormuz 0.12, Malacca 0.20}`
* duration `Uniform(0.25, 1.0)` of the year; extra COGS `Beta(2,5) · β_k · e_k · s_k` with `β = {0.10, 0.08, 0.06, 0.04}`
* `FCF = FCF₀ − lost revenue × gross margin − extra COGS`

Outputs: revenue/FCF shock percentiles, `P(FCF drawdown > 20%)`, histogram. Same inputs ⇒ identical numbers.

## 5. Metrics

| Metric | Formula |
|---|---|
| **CPI** Chokepoint Paralysis Index | `0.40·V4W + 0.35·MineralHHI + 0.25·RouteStress`; `V4W = 0.30W + 0.20Wi + 0.15Wa + 0.35We`; MineralHHI = mean of the top-3 country HHI/100 of minerals named in the filing (sector-implied minerals × 0.6); RouteStress = route-weighted `(0.6 + 0.4·clip(−Δtransits/30%, 0, 1))` × `(0.5 + 0.5·Asia share)` × 100 |
| **GPI** Geopolitical Vulnerability Index | `0.35·ExportCtrl + 0.25·ChinaTaiwanShare + 0.20·Tariff + 0.20·ScreeningHits` |
| **SRS** Stranded Risk Score | `0.50·P(FCF↓>20%) + 0.30·|p05 revenue shock|/30% + 0.20·min(100, 200·PP&E/assets)` |
| **FAR** Friend-Shoring Alpha Rating | `0.45·(100−GPI) + 0.30·(100−CPI) + 0.25·Resilience`, Resilience `= 50 + 25·sat(cash/revenue, 0.25) + 25·clip(FCF margin/25%, −1, 1)`; bands AAA ≥ 85, AA ≥ 75, A ≥ 65, BBB ≥ 55, BB ≥ 45, B ≥ 35, else CCC |

Recommendation policy: **REDUCE** if SRS ≥ 60 or (CPI ≥ 60 and GPI ≥ 60); **HEDGE** if SRS ≥ 40 or GPI ≥ 55 or CPI ≥ 55; **ACCUMULATE** if FAR ≥ 65; otherwise **HOLD**.

## 6. Agents and verification

1. **Orchestrator** plans tool calls and hypotheses (Claude via Vercel AI Gateway, or a fixed plan).
2. **4W Graph agent** calls SEC / USGS / PortWatch / CSL / Comtrade tools and extracts supply-chain triples; a triple is kept only if its object literally appears in the cited sentence.
3. **Simulation agent** computes derived fundamentals and the stress test in the sandbox.
4. **Synthesizer** drafts the memo from a fact sheet of ledger entries.
5. **Red-Team** (a) code verifier: every `[E#]` must exist; every number must equal a ledger value after unit scaling (±0.6% or ±0.05 for rounding); every “quoted” span must appear verbatim in a ledger quote; the recommendation must follow the policy; (b) an adversarial critique whose counterpoints pass the same verifier. Failures send the memo back to the Synthesizer (max 2 replans); anything still failing is removed.

## 7. Scope and roadmap

* Exposure scores measure *disclosed* risk intensity; every score opens to the exact sentences behind it so the analyst sees the evidence, not just the number.
* Coverage: all SEC registrants (≈10k issuers, including foreign private issuers filing 20-F such as TSMC and ASML).
* Scenario parameters (`L_k`, `β_k`) are a documented library the analyst can tune. Roadmap v2: calibrate them on historical shocks (2011 Thai floods, 2021 Suez blockage, 2023–24 Red Sea diversions) using PortWatch history, and add KRX/DART issuers for Korean holdings.
