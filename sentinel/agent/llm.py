"""LLM access through the Vercel AI Gateway (OpenAI-compatible Chat Completions).

Auth order: AI_GATEWAY_API_KEY env → VERCEL_OIDC_TOKEN env → the per-request
`x-vercel-oidc-token` header captured by the API layer. With none available the
agent runs fully deterministic (planner + template synthesizer); the UI shows
which engine produced a result.
"""

from __future__ import annotations

import contextvars
import json
import os
import re
import threading
import time
from datetime import date
from typing import Any, Dict, List, Optional

import httpx

from sentinel.config import AI_GATEWAY_URL, LLM_DAILY_RUN_CAP, LLM_DISABLED, LLM_FAST_MODEL, LLM_MODEL

request_oidc_token: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("request_oidc_token", default=None)

_lock = threading.Lock()
_runs_today: Dict[str, int] = {}


class LLMUnavailable(Exception):
    pass


_last_request_token: Dict[str, Any] = {"tok": None, "ts": 0.0}


def set_request_token(tok: str) -> None:
    """Vercel passes a short-lived OIDC token on every request header; keep the latest."""
    _last_request_token["tok"] = tok
    _last_request_token["ts"] = time.time()


def _token() -> Optional[str]:
    env = os.getenv("AI_GATEWAY_API_KEY") or os.getenv("VERCEL_OIDC_TOKEN")
    if env:
        return env
    if _last_request_token["tok"] and time.time() - _last_request_token["ts"] < 600:
        return _last_request_token["tok"]
    return request_oidc_token.get()


def enabled() -> bool:
    return (not LLM_DISABLED) and bool(_token())


def engine_label() -> str:
    return LLM_MODEL if enabled() else "deterministic"


def take_run_budget() -> bool:
    """Daily cap on LLM-backed runs per instance (protects the free gateway credit)."""
    key = date.today().isoformat()
    with _lock:
        n = _runs_today.get(key, 0)
        if n >= LLM_DAILY_RUN_CAP:
            return False
        _runs_today.clear()
        _runs_today[key] = n + 1
        return True


def chat(
    messages: List[Dict[str, Any]],
    model: Optional[str] = None,
    max_tokens: int = 2000,
    temperature: float = 0.2,
    json_mode: bool = False,
    timeout: float = 60.0,
) -> Dict[str, Any]:
    """Returns {'text', 'model', 'usage', 'ms'}; raises LLMUnavailable on any failure."""
    tok = _token()
    if LLM_DISABLED or not tok:
        raise LLMUnavailable("no gateway credentials")
    body: Dict[str, Any] = {
        "model": model or LLM_MODEL,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    if json_mode:
        body["response_format"] = {"type": "json_object"}
    t0 = time.perf_counter()
    try:
        r = httpx.post(
            f"{AI_GATEWAY_URL}/chat/completions",
            headers={"Authorization": f"Bearer {tok}", "Content-Type": "application/json"},
            json=body,
            timeout=timeout,
        )
    except httpx.HTTPError as exc:
        raise LLMUnavailable(f"gateway error: {exc}") from exc
    if r.status_code != 200:
        raise LLMUnavailable(f"gateway HTTP {r.status_code}: {r.text[:300]}")
    data = r.json()
    try:
        text = data["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError) as exc:
        raise LLMUnavailable(f"bad gateway payload: {str(data)[:300]}") from exc
    return {"text": text, "model": data.get("model", body["model"]), "usage": data.get("usage", {}), "ms": int((time.perf_counter() - t0) * 1000)}


def chat_json(messages: List[Dict[str, Any]], model: Optional[str] = None, max_tokens: int = 2500, timeout: float = 60.0) -> Dict[str, Any]:
    """Chat and parse a JSON object from the reply (tolerates ```json fences)."""
    out = chat(messages, model=model, max_tokens=max_tokens, json_mode=False, timeout=timeout)
    text = out["text"].strip()
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise LLMUnavailable(f"no JSON object in reply: {text[:200]}")
    try:
        obj = json.loads(m.group(0))
    except json.JSONDecodeError as exc:
        raise LLMUnavailable(f"invalid JSON: {exc}") from exc
    return {"json": obj, "model": out["model"], "usage": out["usage"], "ms": out["ms"]}


FAST = LLM_FAST_MODEL
MAIN = LLM_MODEL
