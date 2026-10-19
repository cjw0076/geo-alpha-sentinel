# Geo-Alpha Sentinel

**An open-source, Bloomberg-style terminal where AI agents stress-test a company against the physical chokepoints of 2027–2028, and every number links back to a public primary source.**

**공개 1차 자료에 근거해 2027–2028 물리적 초크포인트(전력·구리·용수·자원무기화)와 지정학 충격을 기업 단위로 진단하는 국부펀드용 오픈소스 AI 에이전트 터미널.**

[Live terminal](https://kic-geo-sentinel.vercel.app) · [API docs](https://kic-geo-sentinel.vercel.app/api/docs) · [Methodology](docs/METHODOLOGY.md) · [Data sources & licenses](docs/DATA_SOURCES.md) · [HTTP API](docs/API.md)

Built for the *Korea Investment Corporation (KIC) AI Agent Competition 2026*.

---

## What it does

Type a ticker (`NVDA <GO>`). In about a second the terminal:

1. pulls the company's **SEC XBRL financials** and its latest **10-K / 20-F risk-factor section**;
2. tags every risk sentence into the **4W framework** — **W**atts (power), **W**ires (copper, grid, packaging), **W**ater, **W**eaponized resources (export controls, sanctions, rare earths);
3. links that exposure to live physical data: **IMF PortWatch** satellite-AIS ship transits through 28 chokepoints (Taiwan Strait, Malacca, Hormuz, Bab el-Mandeb …), **USGS** country concentration of critical minerals, the **U.S. Consolidated Screening List**, and **UN Comtrade** import dependency;
4. runs a seeded **Monte Carlo stress test inside a restricted Python sandbox** for your scenario (Taiwan / China export controls / Hormuz / Malacca sliders);
5. computes four sovereign metrics — **CPI** (Chokepoint Paralysis), **GPI** (Geopolitical Vulnerability), **SRS** (Stranded Risk), **FAR** (Friend-Shoring Alpha Rating, AAA–CCC);
6. lets a **five-agent team** (Orchestrator → 4W Graph → Simulation → Synthesizer → Red-Team) draft a Korean investment-committee memo, where a code verifier rejects any number, quote or evidence tag that is not in the evidence ledger and sends the draft back for a replan.

Every figure on screen and in the exported PDF carries an evidence id. Clicking it opens the source: the XBRL filing, the exact highlighted sentence inside the SEC document (W3C text fragment), the PortWatch series, the USGS table or the screening-list entry.

## Architecture

```
            ┌──────────────── Terminal UI (static, no build) ─────────────────┐
            │ DES · RISK · 4W · STRS · AGNT · MEMO · CHKP · MINR · SANC · MACR · WGLB(3D) │
            └───────────────┬─────────────────────────────────┬──────────────┘
                     REST / JSON                        SSE (agent events)
            ┌───────────────▼─────────────────────────────────▼──────────────┐
            │ FastAPI  (sentinel/api.py)                                       │
            │  Orchestrator ─► 4W Graph agent ─► Simulation agent ─► metrics   │
            │       ▲                (tools)          (sandbox)          │     │
            │       └── replan ◄── Red-Team verifier ◄── Synthesizer ◄───┘     │
            │  Evidence ledger: every number/quote → Provenance(url, license)  │
            └──┬──────────┬───────────┬───────────┬──────────┬─────────┬──────┘
           SEC EDGAR   IMF PortWatch  USGS MCS   Trade.gov   UN       FRED /
           XBRL + 10-K (AIS, live)   2026        CSL        Comtrade  NY Fed GSCPI
```

* **LLM**: Claude Sonnet 5.5 / Haiku 5.5 through the Vercel AI Gateway (OIDC, no key in the repo). Without credentials the identical pipeline runs with a deterministic planner and template synthesizer, and the UI says so.
* **No arithmetic in the model**: margins, FCF, growth, stress statistics and metrics are computed by code; the model only writes prose around ledger values, and the verifier enforces it.

## Run it

```bash
pip install -r requirements.txt
uvicorn app:app --port 8000          # UI at http://localhost:8000, API docs at /api/docs
# optional: export AI_GATEWAY_API_KEY=...  (enables the Claude agents)
# recommended: export SEC_USER_AGENT="Your Org your-email@example.com"   (SEC fair-access policy)
```

Docker: `docker compose up --build` → http://localhost:8000

Tests: `pytest -q` (offline) · `pytest -q -m network` (live sources)

Refresh snapshots: `python etl/build_minerals.py && python etl/build_sanctions.py && python etl/build_macro.py`

## Repository map

| Path | What |
|---|---|
| `sentinel/sources/` | SEC, filings parser, PortWatch, USGS, sanctions screening, Comtrade, macro |
| `sentinel/analytics/` | 4W exposure tagging, metrics, stress program |
| `sentinel/agent/` | LLM gateway client, multi-agent runner, verifier |
| `sentinel/sandbox.py` | restricted deterministic Python execution |
| `sentinel/reports/` | PDF memo and 1200×630 share card |
| `public/` | terminal UI (vanilla ES modules + Three.js globe) |
| `etl/`, `data/` | snapshot builders and committed snapshots with provenance |

## Data & licenses

All inputs are public. SEC EDGAR, USGS and Trade.gov data are U.S. Government works (public domain); IMF PortWatch, UN Comtrade, FRED and the NY Fed GSCPI are used with attribution under their terms. Collection dates, licenses and preprocessing are listed in [docs/DATA_SOURCES.md](docs/DATA_SOURCES.md).

Code: Apache-2.0. Output is research tooling, **not investment advice / 투자 권유 아님**.
