# Geo-Alpha Sentinel — HTTP API (v1)

All schemas are defined in [`sentinel/models.py`](../sentinel/models.py). JSON field names below match those models exactly.
Base path: `/api/v1`. All responses are JSON unless stated otherwise.

| Method | Path | Returns | Notes |
|---|---|---|---|
| GET | `/api/v1/health` | `{status, version, llm:{enabled, model}, sources:{...}}` | |
| GET | `/api/v1/search?q=nvd` | `[{ticker, cik, name}]` (max 12) | SEC `company_tickers.json` (~10k issuers) |
| GET | `/api/v1/company/{ticker}` | `CompanyProfile` | SEC submissions + XBRL company facts |
| GET | `/api/v1/exposure/{ticker}` | `Exposure` | 4W tagging of the latest 10-K / 20-F risk-factor section |
| GET | `/api/v1/chokepoints` | `{as_of, items: [Chokepoint]}` | IMF PortWatch daily transits, 28 chokepoints |
| GET | `/api/v1/minerals` | `{edition, items: [Mineral]}` | USGS Mineral Commodity Summaries snapshot |
| GET | `/api/v1/sanctions?q=Huawei` | `SanctionsScreen` | Trade.gov Consolidated Screening List snapshot |
| GET | `/api/v1/macro` | `{items: [MacroSeries]}` | FRED commodity prices + NY Fed GSCPI |
| GET | `/api/v1/stress/{ticker}?taiwan=&china_export=&hormuz=&malacca=&horizon=` | `{stress: StressResult, metrics: [Metric]}` | Fast path for sliders (no LLM) |
| GET | `/api/v1/analyze?ticker=NVDA&taiwan=0.7&china_export=0.5&hormuz=0.2&malacca=0.2&horizon=2027&question=...` | `text/event-stream` | Full multi-agent run (see below) |
| POST | `/api/v1/memo.pdf` | `application/pdf` | Body: an `AnalysisResult` JSON |
| GET | `/api/v1/og/{ticker}.png?cpi=&gpi=&srs=&far=&shock=` | `image/png` 1200×630 | Share card |

## Server-Sent Events: `/api/v1/analyze`

```
event: agent
data: {AgentEvent}

event: agent
data: {AgentEvent}
...
event: result
data: {AnalysisResult}
```

On failure: `event: error` with `data: {"message": "..."}`. The client should use `EventSource`.

`AgentEvent.type` values and what the UI should do with them:

| type | meaning | `data` payload |
|---|---|---|
| `plan` | orchestrator plan | `{steps: [{agent, tool, args, why}]}` |
| `tool_call` | an agent calls a tool | `{tool, args}` |
| `tool_result` | tool finished | `{tool, summary, evidence_ids: [..], ms}` |
| `thought` | short reasoning note | `null` |
| `metric` | a metric was computed | `Metric` |
| `verification` | red-team / verifier check | `{passed: bool, checked: int, rejected: [{text, reason}]}` |
| `replan` | verifier forced another loop | `{attempt, reason}` |
| `memo` | memo drafted | `Memo` |
| `done` | run finished | `{ms, engine}` |
