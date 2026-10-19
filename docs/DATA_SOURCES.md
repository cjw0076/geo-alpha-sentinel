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
| **FRED Commodity Prices** | 세인트루이스 연준 (FRED) & IMF | 실시간 CSV (TTL 24h) + 스냅샷 | 2026-10-14 | Public Domain / IMF Primary Commodity Prices | 구리, 알루미늄, 니켈, 천연가스 월별 시계열 추출, 일별 브렌트유(`DCOILBRENTEU`) 월말 평균 리샘플링, 60개월 12M YoY % 산출 |
| **NY Fed GSCPI** | 뉴욕 연방준비은행 | 공식 엑셀 스냅샷 | 2026-09-30 | Attribution (Free use with attribution) | 글로벌 공급망 압력지수 345개월 시계열 파싱, 60개월 윈도우 보존, 12M YoY % 산출 |
| **UN Comtrade** | 유엔 통계국 (UNSD) | 실시간 미리보기 API (TTL 7d) | 2024 / 2023 | Non-commercial with attribution | 미국(842) 대상 9대 전략품목(반도체, 배터리, 자석, 구리 등) 수입액 집계, 월드(0) 분리, 상위 10개국 점유율 및 수입 HHI 계산 |
| **SEC EDGAR** | 미국 증권거래위원회 (SEC) | 실시간 REST API (Live) | 실시간 (FY2025/26) | Public Domain (U.S. Government work) | CIK 매핑, Company Facts XBRL 재무제표(매출, 영업이익, CFO, Capex) 추출, 10-K Item 1A 위험요인 텍스트 프래그먼트 링크 생성 |

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

## 4. St. Louis Fed FRED Macroeconomic Time Series

### Overview & Description
Federal Reserve Economic Data (FRED) is an online database maintained by the Research Division of the Federal Reserve Bank of St. Louis, providing historical commodity benchmark series sourced from the International Monetary Fund (IMF Primary Commodity Price System).

- **Primary Source URL**: https://fred.stlouisfed.org/
- **Series Monitored**:
  - `PCOPPUSDM`: Global Price of Copper (USD/metric ton, monthly)
  - `PALUMUSDM`: Global Price of Aluminum (USD/metric ton, monthly)
  - `PNICKUSDM`: Global Price of Nickel (USD/metric ton, monthly)
  - `DCOILBRENTEU`: Crude Oil Prices: Brent - Europe (USD/barrel, daily)
  - `PNGASEUUSDM`: Global Price of Natural Gas, EU (USD/MMBtu, monthly)
- **Collection Method**: Live CSV queries via `sentinel/http.py` with 24-hour cache TTL; automated fallback to `data/macro.json`.
- **License**: Public domain / IMF Primary Commodity Prices via FRED.
- **Preprocessing Pipeline**:
  1. Fetches raw time series via automated CSV endpoints.
  2. Resamples daily Brent series into month-end arithmetic means.
  3. Retains rolling 60-month historical observations.
  4. Calculates 12-month trailing year-over-year percentage change: $\text{YoY} = \left(\frac{V_t}{V_{t-12}} - 1.0\right) \times 100$.
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
- **Refresh Command**:
  ```bash
  python3 -c "from sentinel.sources.comtrade import import_dependency; r = import_dependency('8542'); print(r['hs'], r['total_usd'], r['hhi'])"
  ```

---

## 7. Zero-Hallucination Institutional Provenance Guarantee

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
