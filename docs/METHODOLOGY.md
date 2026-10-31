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

## 8. Portfolio lens

Code: `sentinel/sources/holdings.py` (books), `sentinel/analytics/portfolio.py` (aggregation, view), `PORTFOLIO_STRESS_PROGRAM` in `sentinel/analytics/stress.py`, `etl/build_portfolio.py` (snapshots in `public/snapshots/portfolio/`). Methodology version `portfolio-1.0`.

**Books.**
* *KIC US equities (13F)* — the latest Form 13F-HR of Korea Investment Corp (CIK 0001441689). Every `infoTable` row of the information-table XML is parsed (issuer, class, CUSIP, value, shares, put/call). Value units are checked twice: the sum of `value` must equal the cover-page `tableValueTotal` (±0.5%; a 1/1000 ratio means the table is in thousands), and the median implied price per share (`value / shares`) below $1 means thousands; filings from 2023-01-03 report dollars. Rows are aggregated by CUSIP; option rows (`putCall`) are listed separately and carry no weight. `w_i = value_i / Σ value` over non-option rows.
* *MSCI ACWI ex Korea proxy* — the latest Form N-PORT-P of the series "iShares MSCI ACWI ETF" (iShares Trust, CIK 0001100663; series id from SEC `company_tickers_mf.json`, verified against `genInfo/seriesName`). Rows with `assetCat = EC` (equity common) are kept, rows with `invCountry = KR` are removed, and the remaining values are renormalised to 1.
* *Coverage universe* — the 40 issuers of `etl/build_snapshots.py`, equal weight (a second book for the UI).

**Identifiers.** CUSIP (or ISIN; US/CA ISINs are reduced to their CUSIP) → US ticker through the OpenFIGI v3 mapping API (no key: ≤ 10 jobs per request, ≤ 25 requests per minute), US composite (`exchCode = US`) listing preferred; the ticker is kept only if it resolves in SEC `company_tickers.json`. Otherwise a unique match of the normalised issuer name (upper case, punctuation removed, legal-form suffixes such as INC / CORP / CO / LTD / PLC / AG / NV / SA removed) against SEC `company_tickers.json`. Every row records `map_method` (`openfigi`, `sec_name`, `none`). OpenFIGI results are stored in `data/identifier_map.json`, so reruns do not call OpenFIGI again. A row is an SEC filer when its ticker resolves and the issuer's latest 10-K, 20-F or 40-F on EDGAR (`sec.latest_annual_filing`) was filed no more than 550 days before the book date (foreign issuers that deregistered keep their last 20-F on EDGAR for years). Rows are then merged to issuer positions by CIK (share classes such as GOOGL/GOOG become one position).

**Coverage rule.** Book: the largest positions by weight until the cumulative weight reaches 90% or 300 names, whichever comes first. Benchmark: the 500 largest SEC-filer positions by weight. Positions without an SEC ticker, without a current annual report (for example ETFs held in the 13F book) or whose single-issuer analysis fails are recorded in `failures`. Input checks also send an issuer to `failures` instead of the aggregate: no USD revenue in XBRL company facts; gross margin outside [−1, 1]; latest XBRL revenue year older than the book year − 2; annual report filed more than 550 days before the book date (`portfolio.quality_issue`). `covered_weight` is the original weight of the analysed positions. **Every aggregate below is computed on the covered names renormalised to 1** (`w̃_i = w_i / Σ_covered w_j`, likewise `b̃_i`), and the JSON says so in `coverage_note`.

**Issuer vectors.** For each covered issuer the single-issuer pipeline supplies the four channel exposures `e_ik ∈ [0,1]` (§3), revenue, gross margin, FCF and base fiscal year (as used by §4), CPI/GPI/SRS/FAR (§5) and the policy verdict (§5 recommendation policy). Portfolio numbers therefore use the same issuer numbers as the drill-down.

**Channel exposure.** For `k ∈ {taiwan, china_export, hormuz, malacca}`:
* `E_k = Σ_i w̃_i · e_ik` (book), `B_k = Σ_i b̃_i · e_ik` (benchmark), shown in percent (`book_pct = 100·E_k`).
* Active exposure `active_pp = 100 · (E_k − B_k)` percentage points.
* Contributions `c_ik = w̃_i · e_ik` (they sum to `E_k`); active contributions `(w̃_i − b̃_i) · e_ik` on the union of both covered sets (they sum to `E_k − B_k`).
* Concentration of contributions: `HHI_k = Σ_i (c_ik / E_k)²`, `effective_n_k = 1 / HHI_k`.
* `book_pct` is an exposure-weighted average score, not a share of holdings. The share of holdings behind a channel is reported separately: `book_material_pct = 100 · Σ_i w̃_i · 1[e_ik ≥ 0.5]` (weight share of covered holdings whose exposure is at least 0.5), `bench_material_pct` likewise with `b̃_i`, and `material_active_pp` = their difference. The threshold 0.5 is the midpoint of the 0–1 exposure scale; on the saturating map of §3 it is reached at about 0.69 × the channel scale (e.g. Taiwan: ≈ 5.5 weighted Taiwan signals, Hormuz: ≈ 5.5, China export controls: ≈ 6.9, Malacca: ≈ 8.3).
* A reviewer can recompute `book_pct` from the `holdings` array: `100 · Σ weight_pct·chans_k / Σ weight_pct`.

**Joint scenario (common shock).** `PORTFOLIO_STRESS_PROGRAM`, sandbox-executed, seeded (`MC_SEED`, 42), 20,000 paths, same parameters as §4 (`L_k`, `β_k`, severities `s_k`, horizon). For every path and channel the event draw `u_k ~ U(0,1)`, depth `D_k ~ Beta(2,3)`, duration `T_k ~ U(0.25,1)` and cost multiplier `C_k ~ Beta(2,5)` are drawn **once and shared by all issuers**, in the same order as `STRESS_PROGRAM`. Issuer *i* is hit when `u_k < p_ik = min(0.95, s_k (0.55 + 0.10·years_i))` (its own base year), so one event path hits every exposed issuer together. Then, per issuer:
* revenue shock `R_i = max(−0.95, −Σ_k hit_ik · D_k · L_k · e_ik · (0.5 + 0.5 s_k) · T_k)`
* extra COGS share `K_i = Σ_k hit_ik · C_k · β_k · e_ik · s_k`
* FCF shock `x_i = (−R_i · Rev_i · GM_i − K_i · COGS_i) / |FCF_i|` (as in §4; `FCF_i = 0` is replaced by 1% of revenue)
* portfolio FCF shock `X = Σ_i w̃_i · max(x_i, −100%)` (book) and `Y = Σ_i b̃_i · max(x_i, −100%)` (benchmark) on the same paths. The floor means a holding can lose at most its whole base FCF inside the aggregate: `x_i` is measured against `|FCF_i|` and is unbounded when FCF is near zero (without the floor one benchmark issuer with near-zero FCF moved the benchmark P05 to the order of −10⁸ %). Issuer marginals (`p05_pct`) are reported without the floor; `stress.floor_binding_at_p05` counts the issuers whose own P05 is below −100% and their weights.

Reported for book and benchmark: P05, P50, mean, ES95 (mean of the worst 5% of paths) and `p_below_20` = share of paths with `X < −20%` (in percent); `active_p05_pp` = 5th percentile of `X − Y`. Because every issuer draws the same random numbers in the same order as `STRESS_PROGRAM`, each issuer's marginal distribution equals its own `run_stress` result; a one-issuer book with weight 1 reproduces `run_stress` P05/P50 (test: within 1.0 pp; measured difference 0 with the floor disabled, and equal to `max(−100%, run_stress)` with it). The common-shock assumption makes channel events perfectly co-occurring across issuers; issuer-level differences come only from exposures, base years and fundamentals.

**Holdings to act on.** Covered book names are ranked twice: (1) contribution to the book's tail, `w̃_i · mean(x_i over the book's worst 5% of paths)` (these contributions sum to the book's ES95), most negative first; (2) active exposure `Σ_k (w̃_i − b̃_i) · e_ik`, largest first. The five names with the smallest rank sum (ties: tail contribution) are listed in `actions` with the issuer's policy verdict (REDUCE 노출 축소 검토 / HEDGE 헤지 검토 / HOLD 모니터링 / ACCUMULATE 여력 있음), its joint-scenario FCF P05 and a one-sentence reason in which every number carries its evidence tag (checked by the verifier, §6). The reason names the issuer's dominant channel: the largest `e_ik · L_k · (0.5 + 0.5 s_k) · p_ik`. `verdict_distribution` counts the verdicts over all covered book names.

**Headline.** Among the channels' chokepoints (Taiwan Strait `chokepoint11`, Strait of Hormuz `chokepoint6`, Malacca Strait `chokepoint5`; the China export-control channel has none) with a 365-day baseline ≥ 10 ships/day, the one with the largest absolute shortfall `baseline − 7-day mean` among Δ ≤ −25% is chosen (same rule as the UI's `anomaly()`); if none qualifies, the most negative Δ. Headline: `{chokepoint} 통항 {Δ}% — {book}의 {book_material_pct}%(비중)가 이 경로에 실질 노출(노출도 ≥ 0.5), 벤치마크 {bench_material_pct}%` (EN: `{chokepoint} transits {Δ}% vs 365-day baseline — {book_material_pct}% of {book} (by weight) materially exposed to this route (exposure ≥ 0.5), benchmark {bench_material_pct}%`), with evidence ids for Δ, both shares and the threshold.

**Evidence.** Each JSON file carries its own `evidence` array (ids `E1…` unique within the file): E1 the book's filing (or the coverage-universe definition), E2 the benchmark filing, PortWatch chokepoint readings, channel aggregates, issuer exposures (linked to each issuer's annual report), holding weights (linked to the 13F / N-PORT filing) and sandbox outputs (with the program text).
