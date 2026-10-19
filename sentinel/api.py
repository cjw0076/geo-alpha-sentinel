"""HTTP API (FastAPI). Static UI is served from /public by the platform CDN on
Vercel, or by StaticFiles when running locally / in Docker."""

from __future__ import annotations

import json
import os
import re
import threading
import time
from collections import defaultdict, deque
from typing import Any, Deque, Dict, Iterator, Optional

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse, Response, StreamingResponse

from sentinel import service
from sentinel.agent import llm
from sentinel.config import ROOT, VERSION
from sentinel.models import AgentEvent, AnalysisResult, Scenario
from sentinel.sources import sec

app = FastAPI(
    title="Geo-Alpha Sentinel API",
    version=VERSION,
    description="Evidence-bound multi-agent chokepoint & geopolitical risk analysis (open source). Not investment advice.",
    docs_url="/api/docs",
    openapi_url="/api/openapi.json",
)
app.add_middleware(GZipMiddleware, minimum_size=2048)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["GET", "POST"], allow_headers=["*"])

TICKER_RE = re.compile(r"^[A-Za-z0-9.\-]{1,12}$")


@app.middleware("http")
async def capture_oidc(request: Request, call_next):
    tok = request.headers.get("x-vercel-oidc-token")
    if tok:
        llm.set_request_token(tok)
    return await call_next(request)


def _ticker(t: str) -> str:
    if not TICKER_RE.match(t or ""):
        raise HTTPException(400, "invalid ticker")
    return sec.normalize_ticker(t)


def _cached(body: Any, seconds: int) -> JSONResponse:
    return JSONResponse(body, headers={"Cache-Control": f"public, max-age={min(seconds, 300)}, s-maxage={seconds}, stale-while-revalidate={seconds * 4}"})


def _scenario(taiwan: float, china_export: float, hormuz: float, malacca: float, horizon: int, question: Optional[str] = None) -> Scenario:
    try:
        return Scenario(taiwan=taiwan, china_export=china_export, hormuz=hormuz, malacca=malacca, horizon=horizon, question=(question or None))
    except Exception as exc:
        raise HTTPException(422, f"invalid scenario: {exc}")


# ---------------------------------------------------------------------------
# Rate limit for the expensive endpoint (per instance, per client IP)
# ---------------------------------------------------------------------------

_hits: Dict[str, Deque[float]] = defaultdict(deque)
_hits_lock = threading.Lock()


def _rate_ok(ip: str, limit: int = 8, window: float = 60.0) -> bool:
    now = time.time()
    with _hits_lock:
        q = _hits[ip]
        while q and now - q[0] > window:
            q.popleft()
        if len(q) >= limit:
            return False
        q.append(now)
        return True


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.get("/api/v1/health")
def health() -> Dict[str, Any]:
    data_files = {p.name: p.stat().st_size for p in (ROOT / "data").glob("*") if p.is_file()} if (ROOT / "data").exists() else {}
    return {
        "status": "ok",
        "version": VERSION,
        "llm": {"enabled": llm.enabled(), "model": llm.MAIN if llm.enabled() else None, "fast_model": llm.FAST if llm.enabled() else None},
        "sources": {
            "sec_edgar": "live", "imf_portwatch": "live", "un_comtrade": "live (public preview)", "fred": "live + snapshot",
            "usgs_mcs": "snapshot", "trade_csl": "snapshot", "nyfed_gscpi": "snapshot",
        },
        "data_files": data_files,
    }


@app.get("/api/v1/search")
def search(q: str = Query("", max_length=40)):
    return _cached(sec.search(q), 86400)


@app.get("/api/v1/company/{ticker}")
def company(ticker: str):
    try:
        return _cached(service.profile(_ticker(ticker)).model_dump(), 3600)
    except LookupError as exc:
        raise HTTPException(404, str(exc))


@app.get("/api/v1/exposure/{ticker}")
def exposure(ticker: str):
    try:
        return _cached(service.exposure(_ticker(ticker)).model_dump(), 6 * 3600)
    except LookupError as exc:
        raise HTTPException(404, str(exc))


@app.get("/api/v1/chokepoints")
def chokepoints():
    as_of, items = service.chokepoints()
    return _cached({"as_of": as_of, "items": [c.model_dump() for c in items]}, 3 * 3600)


@app.get("/api/v1/minerals")
def minerals():
    edition, items = service.minerals()
    return _cached({"edition": edition, "items": [m.model_dump() for m in items]}, 86400)


@app.get("/api/v1/sanctions")
def sanctions(q: str = Query(..., min_length=2, max_length=120)):
    from sentinel.sources import sanctions as src

    return _cached(src.screen(q).model_dump(), 86400)


@app.get("/api/v1/macro")
def macro():
    from sentinel.sources import macro as src

    return _cached({"items": [m.model_dump() for m in src.get_macro()]}, 6 * 3600)


@app.get("/api/v1/stress/{ticker}")
def stress(
    ticker: str,
    taiwan: float = Query(0.5, ge=0, le=1),
    china_export: float = Query(0.5, ge=0, le=1),
    hormuz: float = Query(0.2, ge=0, le=1),
    malacca: float = Query(0.2, ge=0, le=1),
    horizon: int = Query(2027, ge=2026, le=2030),
):
    sc = _scenario(taiwan, china_export, hormuz, malacca, horizon)
    try:
        st, mets, chans = service.stress_and_metrics(_ticker(ticker), sc)
    except LookupError as exc:
        raise HTTPException(404, str(exc))
    return _cached({"stress": st.model_dump(), "metrics": [m.model_dump() for m in mets], "exposure_by_channel": chans}, 3600)


# ---- multi-agent run (SSE) -------------------------------------------------

_runs = service.TTLCache(6 * 3600, maxsize=128)


def _sse(event: str, data: Any) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False, separators=(',', ':'))}\n\n"


@app.get("/api/v1/analyze")
def analyze(
    request: Request,
    ticker: str,
    taiwan: float = Query(0.5, ge=0, le=1),
    china_export: float = Query(0.5, ge=0, le=1),
    hormuz: float = Query(0.2, ge=0, le=1),
    malacca: float = Query(0.2, ge=0, le=1),
    horizon: int = Query(2027, ge=2026, le=2030),
    question: Optional[str] = Query(None, max_length=600),
):
    t = _ticker(ticker)
    sc = _scenario(round(taiwan, 2), round(china_export, 2), round(hormuz, 2), round(malacca, 2), horizon, question)
    key = (t, sc.taiwan, sc.china_export, sc.hormuz, sc.malacca, sc.horizon, (sc.question or "").strip().lower(), llm.enabled())
    cached: Optional[AnalysisResult] = _runs.get(key)
    ip = (request.headers.get("x-forwarded-for") or (request.client.host if request.client else "?")).split(",")[0].strip()

    def replay() -> Iterator[str]:
        yield ": cached run, replaying\n\n"
        prev = 0
        for e in cached.events:
            time.sleep(min(0.12, max(0.0, (e.t_ms - prev) / 1000)))
            prev = e.t_ms
            yield _sse("agent", e.model_dump())
        yield _sse("result", cached.model_dump())

    def live() -> Iterator[str]:
        from sentinel.agent.runner import run_analysis

        yield ": stream open\n\n"
        try:
            for item in run_analysis(t, sc):
                if isinstance(item, AgentEvent):
                    yield _sse("agent", item.model_dump())
                else:
                    _runs.set(key, item)
                    yield _sse("result", item.model_dump())
        except Exception as exc:  # surfaced to the UI, never a silent hang
            yield _sse("error", {"message": f"{type(exc).__name__}: {str(exc)[:300]}"})

    if cached is not None:
        gen = replay()
    elif not _rate_ok(ip):
        def limited() -> Iterator[str]:
            yield _sse("error", {"message": "Rate limit: 8 live runs per minute per client. Cached scenarios still replay instantly."})
        gen = limited()
    else:
        gen = live()
    return StreamingResponse(gen, media_type="text/event-stream", headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"})


@app.post("/api/v1/memo.pdf")
async def memo_pdf(request: Request):
    from sentinel.reports.memo_pdf import render_memo_pdf

    raw = await request.body()
    if len(raw) > 6_000_000:
        raise HTTPException(413, "payload too large")
    try:
        result = AnalysisResult(**json.loads(raw))
    except Exception as exc:
        raise HTTPException(422, f"invalid AnalysisResult: {str(exc)[:200]}")
    pdf = render_memo_pdf(result)
    return Response(pdf, media_type="application/pdf", headers={"Content-Disposition": f'attachment; filename="GeoAlphaSentinel_{result.ticker}_memo.pdf"'})


@app.get("/api/v1/og/{ticker}.png")
def og(ticker: str, cpi: Optional[float] = None, gpi: Optional[float] = None, srs: Optional[float] = None, far: Optional[str] = None, far_score: Optional[float] = None, shock: Optional[float] = None, rec: Optional[str] = None):
    from sentinel.reports.og_card import render_og_png

    t = _ticker(ticker)
    try:
        p = service.profile(t)
        name = p.name
        if cpi is None or gpi is None or srs is None or far is None:
            st, mets, _ = service.stress_and_metrics(t, Scenario())
            mm = {m.code: m for m in mets}
            cpi, gpi, srs = mm["CPI"].value, mm["GPI"].value, mm["SRS"].value
            far, far_score = mm["FAR"].rating, mm["FAR"].value
            shock = st.revenue_shock_pct.get("p05") if shock is None else shock
    except LookupError:
        raise HTTPException(404, "unknown ticker")
    png = render_og_png(t, name, cpi, gpi, srs, far or "-", far_score or 0.0, shock, rec)
    return Response(png, media_type="image/png", headers={"Cache-Control": "public, max-age=3600, s-maxage=86400"})


# ---- local / Docker: serve the static UI ------------------------------------

if not os.getenv("VERCEL"):
    from fastapi.staticfiles import StaticFiles

    public = ROOT / "public"
    if public.exists():
        app.mount("/", StaticFiles(directory=str(public), html=True), name="ui")
