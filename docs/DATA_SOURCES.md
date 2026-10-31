# Data Sources & ETL Provenance Specification (데이터 출처 및 전처리 명세서)

> **KIC Geo-Alpha Sentinel Platform**  
> Korea Investment Corporation (KIC) AI Agent Competition  
> Compliance: All datasets adhere strictly to primary institutional sources, reproducible ETL pipelines, public domain / fair use licensing, and verified zero-fixture determinism.

---

## 🇰🇷 대회 심사 규정 준수 데이터 총괄 요약표 (Summary Table)

| 데이터 명칭 | 공공/기관 출처 (Source) | 수집 방식 및 주기 | 수집일 / 기준일 | 라이선스 (License) | 전처리 및 가공 방법 (Preprocessing) |
| :--- | :--- | :--- | :---: | :--- | :--- |
| **IMF PortWatch Daily Chokepoints** | 국제통화기금 (IMF) & 옥스퍼드대 | 실시간 REST API (TTL 6h) | 2026-10-11 | Attribution (Free to use with attribution) | 28개 병목 해협 메타데이터 매핑, 500일 일별 통항량 페이지네이션, 7일 평균 및 365일 베이스라인 산출, Z-score 및 Delta % 계산 |
| **USGS MCS 2026** | 미국 지질조사국 (USGS) | 공식 PDF 스냅샷 (`etl/`) | 2026-01-30 (MCS 2026) | Public Domain (U.S. Government work) | 20개 핵심 광물 PDF 레이아웃 파싱 (`pdftotext`), 2025e 국가별 생산량 및 점유율 계산, HHI(0-10,000) 산출, 미국 순수입의존도(NIR) 추출 |
| **Trade.gov CSL** | 미국 상무부 (ITA) | 압축 스냅샷 (`.json.gz`) | 2026-10-19 | Public Domain (U.S. Government work) | Entity만 선별 (선박 제외), 16,618건 기업 정제, 법인 접미사 13종 제거 토큰 정규화, 역색인 및 SequenceMatcher <130ms 검색 |
| **World Bank Commodity Prices (Pink Sheet)** | 세계은행 (World Bank Prospects Group) | 월간 엑셀 (주 1회 캐시) + 스냅샷 | 2026-10-31 (기준월 2026-09) | CC BY 4.0 (출처 표기) | 'Monthly Prices' 시트에서 브렌트유·유럽 천연가스·구리·알루미늄·니켈 월평균가 추출, 기간 `YYYYMmm` → 월말일 변환, 60개월 보존, 12M YoY % 산출 |
| **NY Fed GSCPI** | 뉴욕 연방준비은행 | 공식 엑셀 스냅샷 | 2026-09-30 | Attribution (Free use with attribution) | 글로벌 공급망 압력지수 345개월 시계열 파싱, 60개월 윈도우 보존, 12M YoY % 산출 |
| **UN Comtrade** | 유엔 통계국 (UNSD) | 실시간 미리보기 API (TTL 7d) | 2024 / 2023 | Non-commercial with attribution | 미국(842) 대상 9대 전략품목(반도체, 배터리, 자석, 구리 등) 수입액 집계, 월드(0) 분리, 상위 10개국 점유율 및 수입 HHI 계산 |
| **SEC EDGAR** | 미국 증권거래위원회 (SEC) | 실시간 REST API (Live) | 실시간 (FY2025/26) | Public Domain (U.S. Government work) | CIK 매핑, Company Facts XBRL 재무제표(매출, 영업이익, CFO, Capex) 추출, 10-K Item 1A 위험요인 텍스트 프래그먼트 링크 생성 |
| **SEC Form 13F-HR (KIC)** | 미국 증권거래위원회 (SEC EDGAR) — 한국투자공사 CIK 0001441689 | 정보표 XML (`etl/build_portfolio.py`, 분기) | 2026-10-31 수집 / 2026-06-30 기준 (2026-08-12 제출) | Public domain (SEC filing) | infoTable 677행 파싱, 표지 `tableValueTotal`과 합계 일치·주당 환산가로 달러/천달러 판정, CUSIP별 합산, 옵션행 비중 제외, 비중 = 평가액/합계 |
| **SEC Form N-PORT-P (iShares MSCI ACWI ETF)** | SEC EDGAR — iShares Trust CIK 0001100663, 시리즈 S000021461 | `primary_doc.xml` (`etl/build_portfolio.py`) | 2026-10-31 수집 / 2026-07-31 기준 (2026-09-23 제출) | Public domain (SEC filing) | assetCat=EC 행만 유지, invCountry=KR 제거, 1로 재정규화 → MSCI ACWI ex Korea 대용 벤치마크 |
| **OpenFIGI v3 mapping** | Bloomberg OpenFIGI | REST API (키 없음, 요청당 10건·분당 25회) → `data/identifier_map.json` 저장 | 2026-10-31 | FIGI 식별자 public domain (OpenFIGI ToS §1), as is, 보증·추천 아님 | CUSIP/CINS/ISIN → 미국 통합(US) 티커, SEC `company_tickers.json` 대조, 실패 시 정규화 회사명 일치 |
| **Daily adjusted close (event validation only)** | Yahoo Finance chart API | 실행 시 조회 + 로컬 디스크 캐시 (`CACHE_DIR`) | 2018-03 – 2026-04 | Yahoo terms of service; 원시 가격 재배포 없음 | 로그수익률 → 시장모형(ACWI) 비정상수익률 → CAR, IC, 5분위 스프레드. **원시 가격은 저장소·`public/`에 기록하지 않고 파생 통계만 공개** |

---

## 1. IMF PortWatch Daily Chokepoint Transits

### Overview & Description
IMF PortWatch is a collaborative initiative between the International Monetary Fund and Oxford University that monitors maritime trade flows and bottlenecks using real-time satellite Automatic Identification System (AIS) vessel telemetry.

- **Primary Source URL**: https://portwatch.imf.org/pages/chokepoints
- **REST Endpoints**:
  - Metadata: `https://services9.arcgis.com/weJ1QsnbMYJlCHdG/arcgis/rest/services/PortWatch_chokepoints_database/FeatureServer/0/query`
  - Daily Transits: `https://services9.arcgis.com/weJ1QsnbMYJlCHdG/arcgis/rest/services/Daily_Chokepoints_Data/FeatureServer/0/query`
- **Collection Method**: Live HTTP API via `sentinel/http.py` with multi-tier disk caching (7-day TTL for coordinates, 6-hour TTL for daily transits).
- **License**: Free to use with attribution under IMF PortWatch terms.
- **Preprocessing Pipeline**:
  1. Ingests all 28 monitored maritime bottlenecks (Taiwan Strait, Malacca Strait, Strait of Hormuz, Suez Canal, Panama Canal, Bab el-Mandeb, etc.).
  2. Paginates daily vessel transits (`n_total`, `capacity`) over a rolling 500-day window using `resultOffset` and `resultRecordCount=2000`.
  3. Computes 7-day moving average transit count (`mean_7d`).
  4. Computes 365-day baseline transit average (`baseline_365d`) from the period immediately preceding the 7-day window.
  5. Computes deviation percentage: `delta_pct = ((mean_7d / baseline_365d) - 1.0) * 100`.
  6. Computes standardized anomaly: `zscore = (mean_7d - baseline_365d) / std(baseline)`.
  7. Retains the latest 120 daily observations for micro-chart rendering.
- **Refresh Command**:
  ```bash
  python3 -c "from sentinel.sources.portwatch import get_chokepoints; a, x = get_chokepoints(); print(a, len(x))"
  ```

---

## 2. USGS Mineral Commodity Summaries (MCS 2026)

### Overview & Description
Published annually by the National Minerals Information Center at the U.S. Geological Survey (USGS), Mineral Commodity Summaries is the earliest and most authoritative government assessment of world mine production, reserves, and U.S. import reliance for over 90 critical raw materials.

- **Primary Source URL**: https://pubs.usgs.gov/periodicals/mcs2026/
- **Per-Commodity PDFs**: `https://pubs.usgs.gov/periodicals/mcs2026/mcs2026-<slug>.pdf`
- **Collection Method**: Semi-automated ETL pipeline generating committed snapshot at `data/minerals_usgs_mcs2026.json`.
- **License**: Public domain (U.S. Government work).
- **Target Commodities (20 Critical Materials)**:
  Rare Earths, Gallium, Graphite, Lithium, Cobalt, Nickel, Copper, Tungsten, Antimony, Magnesium Metal, Manganese, Platinum-Group Metals (PGM), Silicon, Tin, Aluminum, Indium, Vanadium, Titanium Minerals (Ilmenite), Helium, Tantalum.
- **Preprocessing Pipeline**:
  1. Fetches official 2-page publication PDFs.
  2. Converts layout preserving tabular coordinate streams using `pdftotext -layout`.
  3. Extracts the **World Mine Production and Reserves** table (or Primary/Refinery production when applicable, e.g., Gallium, Magnesium, Silicon).
  4. Captures 2025 estimated (`2025e`) country production figures and reported `World total (rounded)`.
  5. Computes country percentage market shares: `share_pct = (value / world_total) * 100`.
  6. Computes market concentration via the Herfindahl-Hirschman Index: $\text{HHI} = \sum_{i} (\text{share\_pct}_i)^2 \in [0, 10000]$.
  7. Extracts U.S. Net Import Reliance (`us_net_import_reliance_pct`) from the page 1 Salient Statistics table (`>95` $\rightarrow$ 95.0, `E` $\rightarrow$ 0.0, `W`/`—` $\rightarrow$ null).
  8. Documents omitted records: Germanium is documented as omitted because USGS MCS 2026 withholds country-level tabular data.
- **Refresh Command**:
  ```bash
  python3 etl/build_minerals.py
  ```

---

## 3. U.S. Trade.gov Consolidated Screening List (CSL)

### Overview & Description
The Consolidated Screening List (CSL) is a unified system maintained by the International Trade Administration (Department of Commerce) that aggregates export screening lists from the Departments of Commerce (BIS Entity List, Unverified List, Military End User List), Treasury (OFAC SDN List, Non-SDN CMIC List), and State (ITAR debarred parties, nonproliferation sanctions).

- **Primary Source URL**: https://data.trade.gov/downloadable_consolidated_screening_list/v1/consolidated.csv
- **Collection Method**: Weekly ETL snapshot compressed into `data/csl_entities.json.gz`.
- **License**: Public domain (U.S. Government work).
- **Preprocessing Pipeline**:
  1. Ingests raw 16.8 MB CSV containing >40,000 global entries.
  2. Filters strictly for corporate entities: `type in ('Entity', '')`, explicitly excluding maritime vessels (`vessel_type`, `call_sign`).
  3. Parses primary corporate names, alternate aliases (`alt_names`), sanctioning agency/list, program codes, and 2-letter ISO country codes from address fields.
  4. Compacts data into a 0.44 MB gzip JSON container (16,618 clean entity records).
  5. Implements high-throughput in-memory search in `sentinel/sources/sanctions.py`:
     - Normalizes strings (casefolding, punctuation removal).
     - Strips 13 corporate suffixes (`co`, `ltd`, `inc`, `corp`, `corporation`, `company`, `limited`, `llc`, `group`, `holdings`, `technologies`, `technology`, `the`).
     - Constructs an inverted token index enabling candidate lookup in <2 ms.
     - Scores candidate similarity with `difflib.SequenceMatcher` combined with exact-token subset containment boosting.
     - Returns matches with score $\ge 0.86$ in $<130$ ms.
- **Refresh Command**:
  ```bash
  python3 etl/build_sanctions.py
  ```

---

## 4. World Bank Commodity Price Data (The Pink Sheet)

### Overview & Description
The World Bank Prospects Group publishes monthly benchmark commodity prices in nominal US dollars ("The Pink Sheet"). It replaces the FRED feed used earlier: FRED's terms prohibit storing, caching or archiving FRED content, while the Pink Sheet is licensed CC BY 4.0, which permits storage and redistribution with attribution.

- **Primary Source URL**: https://www.worldbank.org/en/research/commodity-markets (file `CMO-Historical-Data-Monthly.xlsx`, sheet `Monthly Prices`)
- **Series Monitored**:
  - `WB_BRENT`: Crude oil, Brent (USD/bbl)
  - `WB_NGAS_EUR`: Natural gas, Europe (USD/MMBtu)
  - `WB_COPPER`: Copper (USD/mt)
  - `WB_ALUMINUM`: Aluminum (USD/mt)
  - `WB_NICKEL`: Nickel (USD/mt)
- **Collection Method**: `sentinel/sources/macro.py` reads the workbook with a 7-day cache and falls back to `data/macro.json`.
- **License / attribution**: CC BY 4.0 — "World Bank Commodity Price Data (The Pink Sheet)".
- **Preprocessing Pipeline**:
  1. Locates the header row in `Monthly Prices` and the five columns by name.
  2. Converts the period label `YYYYMmm` to the month-end date.
  3. Retains the latest 60 monthly observations.
  4. Calculates 12-month year-over-year change: $\text{YoY} = \left(\frac{V_t}{V_{t-12}} - 1.0\right) \times 100$.
- **Refresh Command**:
  ```bash
  python3 etl/build_macro.py
  ```

---

## 5. Federal Reserve Bank of New York (GSCPI)

### Overview & Description
The Global Supply Chain Pressure Index (GSCPI) developed by the Federal Reserve Bank of New York integrates 27 maritime, air freight, and manufacturing indicators (Baltic Dry Index, Harpex, cross-border supply delivery times, backlogs) to quantify global supply chain disruptions.

- **Primary Source URL**: https://www.newyorkfed.org/research/gscpi.html
- **Direct Download**: `https://www.newyorkfed.org/medialibrary/research/interactives/gscpi/downloads/gscpi_data.xlsx`
- **Collection Method**: Snapshot within `data/macro.json`.
- **License**: Federal Reserve Bank of New York — free use with attribution.
- **Preprocessing Pipeline**:
  1. Ingests official monthly historical index (from 1997 to present, 345 observations).
  2. Converts CFBF container to open standard workbook via LibreOffice.
  3. Extracts date and GSCPI standard deviation values from `GSCPI Monthly Data`.
  4. Retains the latest 60 months and computes 12M YoY velocity.
- **Refresh Command**:
  ```bash
  python3 etl/build_macro.py
  ```

---

## 6. United Nations Comtrade (UN Comtrade)

### Overview & Description
UN Comtrade is the premier international trade database maintained by the United Nations Statistics Division (UNSD), detailing bilateral export and import transactions across Harmonized System (HS) classifications.

- **Primary Source URL**: https://comtradeapi.un.org/public/v1/preview/C/A/HS
- **Partner Lookup URL**: https://comtradeapi.un.org/files/v1/app/reference/partnerAreas.json
- **Collection Method**: Live public preview API via `sentinel/http.py` (7-day TTL for bilateral flows, 30-day TTL for partner reference codes).
- **License**: UN Comtrade — free for non-commercial research use with attribution.
- **HS Watchlist Monitored**:
  - `8542`: Electronic integrated circuits and microassemblies
  - `8541`: Diodes, transistors and semiconductor devices
  - `8486`: Machines and apparatus for the manufacture of semiconductor devices
  - `8507`: Electric accumulators (storage batteries)
  - `8505`: Permanent magnets and articles intended to become permanent magnets
  - `2846`: Compounds, inorganic or organic, of rare-earth metals
  - `8504`: Electrical transformers, static converters and inductors
  - `7403`: Refined copper and copper alloys, unwrought
  - `2825`: Gallium and germanium oxides and other inorganic bases
- **Preprocessing Pipeline**:
  1. Requests import flow records (`flowCode=M`) for reporter 842 (USA) for year 2024, with automated fallback to 2023.
  2. Isolates `partnerCode 0` ('World') to establish aggregate national import demand.
  3. Maps partner numeric codes to geographic partner area names.
  4. Ranks top 10 supplying partner nations by dollar value (`primaryValue`).
  5. Computes market share percentages: $\text{share\_pct} = \left(\frac{\text{value\_usd}}{\text{total\_usd}}\right) \times 100$.
  6. Computes import concentration: $\text{HHI} = \sum_{j} (\text{share\_pct}_j)^2$.

> **Note on Partner Areas**: In accordance with standard UN Comtrade convention, `partnerCode 490` ('Other Asia, nes' — Not Elsewhere Specified) represents Taiwan. In the Sentinel platform, this is explicitly displayed as `Taiwan (Other Asia, nes)` to accurately reflect semiconductor foundry (TSMC) and electronics supply-chain dependencies.

- **Refresh Command**:
  ```bash
  python3 -c "from sentinel.sources.comtrade import import_dependency; r = import_dependency('8542'); print(r['hs'], r['total_usd'], r['hhi'])"
  ```

---

## 7. Daily Prices for Event Validation (Yahoo Finance chart API)

- **Endpoint**: `https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?period1=..&period2=..&interval=1d&events=div,split` (User-Agent `Mozilla/5.0`).
- **Code**: `sentinel/sources/prices.py::daily_adjclose(symbol, start, end)`; used only by `etl/build_eventstudy.py` (`docs/VALIDATION.md`).
- **Collection**: one request per symbol for a fixed range (430 days before the first event to 45 days after the last), paced >= 0.35 s between live requests, disk-cached under `CACHE_DIR` (`SENTINEL_CACHE_DIR`, default `/tmp/sentinel-cache`, not committed).
- **Terms note**: Yahoo Finance data is provided for personal/informational use under Yahoo's terms of service and is not licensed for redistribution. Sentinel therefore publishes **derived statistics only**: cumulative abnormal returns (pp) per issuer and event, rank ICs, quintile spreads and t statistics. Raw price or return series are never written to the repository or to `public/`; `tests/test_eventstudy.py` checks that `public/snapshots/validation/events.json` contains no numeric arrays longer than five values and no price/return keys.
- **Preprocessing**: adjusted close → daily log returns on the ACWI trading calendar → market model (OLS α, β on trading days [−260, −30]) → abnormal returns → CAR[−1,+5] and CAR[−1,+20].

---

## 8. Holdings books for the portfolio lens (SEC Form 13F-HR, SEC Form N-PORT, OpenFIGI)

Code: `sentinel/sources/holdings.py`; build: `python3 etl/build_portfolio.py` (writes `public/snapshots/portfolio/kic13f.json`, `public/snapshots/portfolio/coverage40.json`). Method: `docs/METHODOLOGY.md` §8.

### 8.1 SEC Form 13F-HR — Korea Investment Corporation
- **Source**: EDGAR submissions `https://data.sec.gov/submissions/CIK0001441689.json` → latest `13F-HR` → filing index `https://www.sec.gov/Archives/edgar/data/1441689/<accession>/index.json` → the information-table XML (the `.xml` that is not `primary_doc.xml`) and the cover page `primary_doc.xml`.
- **Retrieved**: 2026-10-31. Filing used: accession 0001193125-26-345356, period 2026-06-30, filed 2026-08-12.
- **License**: Public domain (SEC filing). SEC fair-access policy: declared User-Agent; this ETL limits itself to 4 requests/second (`install_sec_rate_limit`).
- **Preprocessing**: every `infoTable` (nameOfIssuer, titleOfClass, cusip, value, sshPrnamt, sshPrnamtType, putCall) is parsed; `Σ value` must equal the cover `tableValueTotal` (±0.5%, or ×1000 when the table is in thousands); the median implied price per share decides dollars vs thousands (filings from 2023-01-03 report dollars); rows are aggregated by CUSIP; `putCall` rows are kept in `excluded` and carry no weight; weight = value / Σ value of non-option rows. Measured on the filing above: 677 rows, $52,659,999,903 (equal to `tableValueTotal`), median implied price $117.98/share.
- **Limits**: 13F covers Section 13(f) securities only (US-listed equities, ADRs, ETFs), not KIC's full portfolio; ETFs held in the book (e.g. iShares Core S&P 500 ETF) have no annual report and are reported as not covered.

### 8.2 SEC Form N-PORT-P — iShares MSCI ACWI ETF (benchmark proxy)
- **Source**: series id from `https://www.sec.gov/files/company_tickers_mf.json` (symbol ACWI → S000021461); the series' NPORT-P list from EDGAR browse (`cgi-bin/browse-edgar?action=getcompany&CIK=S000021461&type=NPORT-P&output=atom`); `primary_doc.xml` of the latest filing, checked against `genInfo/seriesName = "iShares MSCI ACWI ETF"`.
- **Retrieved**: 2026-10-31. Filing used: accession 0001004726-26-008801, period 2026-07-31, filed 2026-09-23 (`--bench-period YYYY-MM-DD` selects the latest report on or before a date).
- **License**: Public domain (SEC filing).
- **Preprocessing**: `invstOrSec` rows (name, cusip, isin, valUSD, pctVal, invCountry, assetCat or assetConditional) are parsed; only `assetCat = EC` rows are kept; `invCountry = KR` rows are removed; values are renormalised to 1. Measured: 2,273 rows → 2,189 EC → 2,113 after removing 75 Korean rows (2.30% of EC value); 84 non-EC rows dropped (currency, futures, preferred, short-term vehicles, ADR/GDR/units coded `assetConditional OTHER`) and 1 EC row valued 0. Label: "MSCI ACWI ex Korea proxy — iShares MSCI ACWI ETF holdings (SEC N-PORT), Korea removed". This is a holdings-based proxy, not MSCI index data.

### 8.3 OpenFIGI v3 mapping (identifiers → tickers)
- **Endpoint**: `POST https://api.openfigi.com/v3/mapping`, no API key (≤ 10 jobs per request, ≤ 25 requests per minute; the client waits ≥ 2.5 s between requests and backs off on HTTP 429). Jobs: `ID_CUSIP` (or `ID_CINS` for foreign-form CUSIPs whose first character is a letter, or `ID_ISIN`) with `exchCode = US`.
- **Terms and attribution**: OpenFIGI Terms of Service (2018-11-27) §1 dedicates FIGI identifiers to the public domain and permits use, display, reproduction and redistribution; data is provided as is; the "FIGI"/"OpenFIGI" names are used only to refer to the identifiers, with no endorsement implied. Attribution: "Identifier mapping via OpenFIGI.com".
- **Retrieved**: 2026-10-31; stored in `data/identifier_map.json` (one entry per CUSIP/ISIN: US composite ticker, FIGI name, query form, retrieval time, and the resolved SEC ticker/CIK) so that rebuilds do not call OpenFIGI again.
- **Preprocessing**: the US ticker is accepted only if it resolves in SEC `company_tickers.json`, and is replaced by that CIK's first listed ticker (TSMWF → TSM). Rows without such a ticker are matched by normalised issuer name (upper case, punctuation removed, legal-form suffixes removed; a unique SEC match is required). `map_method` (`openfigi`, `sec_name`, `none`) is kept per row. An issuer counts as an SEC filer when it has a 10-K, 20-F or 40-F on EDGAR.

---

## 9. Zero-Hallucination Institutional Provenance Guarantee

Every data record returned by `sentinel.sources.*` conforms to the `Provenance` model:

```python
class Provenance(BaseModel):
    source: Literal["sec_edgar", "imf_portwatch", "usgs_mcs", "trade_csl", "un_comtrade", "fred", "nyfed_gscpi"]
    title: str       # Exact document / filing title
    url: str         # Primary-source verifiable HTTP link
    retrieved_at: str  # ISO-8601 UTC timestamp
    license: str     # Precise terms of use
    locator: str     # Section, table name, column, or query identifier
```

All figures presented in user interfaces, exported PDFs, and simulation models trace 1:1 to these public institutional records. **No mocked constants, synthetic fixtures, or hallucinated values are permitted in production.**
