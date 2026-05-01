"""Proxy routes for the Earnings Vol Analyzer (EVA) service.

Mirrors the pattern in routes_company_evaluator.py. EVA runs separately
(default http://192.168.1.143:8200) and exposes an HTTP API for upcoming
earnings events, per-event feature snapshots, ticker profiles and a
universe management endpoint set. This router proxies those endpoints
through BenTrade's own FastAPI app at /api/eva/*.
"""

import logging
from typing import Optional

import httpx
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

_log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/eva", tags=["earnings-vol-analyzer"])

_TIMEOUT = 30

# Catch both immediate TCP refusal and connection timeouts (e.g. firewall drops)
_EVA_CONNECT_ERRORS = (httpx.ConnectError, httpx.TimeoutException)


# ── Connection mode config (mirrors routes_company_evaluator.py) ────────
EVA_CONFIG: dict = {
    "local": {
        "host": "localhost",
        "port": 8200,
        "label": "Local",
    },
    "remote": {
        "host": "192.168.1.143",
        "port": 8200,
        "label": "Remote (Model Machine)",
    },
}

_eva_connection_mode: str = "remote"


def _get_eva_url() -> str:
    cfg = EVA_CONFIG[_eva_connection_mode]
    return f"http://{cfg['host']}:{cfg['port']}"


def _base_url() -> str:
    """Resolve the EVA base URL from the active connection mode.

    Falls back to settings.EVA_BASE_URL only if the configured host is
    unreachable through the standard mode mapping (kept for back-compat
    with env overrides — though the in-memory mode is now the source of
    truth, mirroring the CE proxy pattern).
    """
    return _get_eva_url().rstrip("/")


def _unavailable_detail() -> dict:
    return {"error": "eva_unreachable", "url": _base_url()}


async def _proxy_get(path: str, params: Optional[dict] = None) -> dict:
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.get(f"{_base_url()}{path}", params=params or {})
            if resp.status_code != 200:
                # Try to surface upstream JSON detail; fall back to text snippet
                try:
                    detail = resp.json()
                except Exception:
                    detail = {"error": "eva_error", "body": resp.text[:500]}
                raise HTTPException(resp.status_code, detail=detail)
            return resp.json()
    except _EVA_CONNECT_ERRORS:
        raise HTTPException(503, detail=_unavailable_detail())
    except HTTPException:
        raise
    except Exception as exc:
        _log.error("event=eva_proxy_failed path=%s error=%s", path, exc)
        raise HTTPException(500, detail=str(exc))


async def _proxy_post(path: str, params: Optional[dict] = None) -> dict:
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(f"{_base_url()}{path}", params=params or {})
            if resp.status_code not in (200, 201, 202, 204):
                try:
                    detail = resp.json()
                except Exception:
                    detail = {"error": "eva_error", "body": resp.text[:500]}
                raise HTTPException(resp.status_code, detail=detail)
            if resp.status_code == 204 or not resp.content:
                return {"ok": True}
            return resp.json()
    except _EVA_CONNECT_ERRORS:
        raise HTTPException(503, detail=_unavailable_detail())
    except HTTPException:
        raise
    except Exception as exc:
        _log.error("event=eva_proxy_failed path=%s error=%s", path, exc)
        raise HTTPException(500, detail=str(exc))


# ── Health & connection ──────────────────────────────────────────────

@router.get("/health")
async def eva_health():
    return await _proxy_get("/api/health")


@router.get("/connection")
async def eva_connection():
    """Return current connection mode and resolved URL (mirrors CE pattern)."""
    cfg = EVA_CONFIG[_eva_connection_mode]
    return {
        "mode": _eva_connection_mode,
        "url": f"http://{cfg['host']}:{cfg['port']}",
        "label": cfg["label"],
        "available_modes": {
            k: {"label": v["label"], "url": f"http://{v['host']}:{v['port']}"}
            for k, v in EVA_CONFIG.items()
        },
    }


@router.post("/connection")
async def set_eva_connection(body: dict):
    """Switch the EVA connection mode (local / remote)."""
    global _eva_connection_mode
    mode = body.get("mode")
    if mode not in EVA_CONFIG:
        raise HTTPException(400, detail=f"Unknown mode: {mode}. Valid: {list(EVA_CONFIG.keys())}")
    _eva_connection_mode = mode
    cfg = EVA_CONFIG[mode]
    _log.info("event=eva_connection_changed mode=%s url=http://%s:%s", mode, cfg["host"], cfg["port"])
    return {
        "ok": True,
        "mode": mode,
        "url": f"http://{cfg['host']}:{cfg['port']}",
        "label": cfg["label"],
    }


@router.get("/status")
async def eva_status():
    """Lightweight reachability probe for the active EVA target.

    Mirrors /api/company-evaluator/status (used by the CE switch UI to
    show a green/red dot next to the URL).
    """
    url = _base_url()
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(f"{url}/api/health")
            healthy = resp.status_code == 200
    except Exception:
        healthy = False
    return {"url": url, "service_healthy": healthy, "mode": _eva_connection_mode}


# ── Events ───────────────────────────────────────────────────────────

@router.get("/events/upcoming")
async def eva_events_upcoming(
    days: int = Query(14, ge=1, le=90),
    ticker: Optional[str] = Query(None),
):
    params: dict = {"days": days}
    if ticker:
        params["ticker"] = ticker.upper()
    return await _proxy_get("/api/events/upcoming", params=params)


@router.get("/events/{event_id}")
async def eva_event_detail(event_id: str):
    return await _proxy_get(f"/api/events/{event_id}")


@router.get("/events/{event_id}/features")
async def eva_event_features(event_id: str):
    return await _proxy_get(f"/api/events/{event_id}/features")


# ── Model analysis ───────────────────────────────────────────────────

@router.post("/events/{event_id}/analyze")
async def analyze_event(event_id: int, force_refresh: bool = Query(False)):
    """Generate structured LLM analysis of an EVA earnings event.

    Pipeline:
      1. Cache hit (file-backed) \u2014 return immediately unless force_refresh.
      2. Fetch /api/events/{id}/analysis-data from EVA via the standard proxy.
      3. Build security-preambled system + user prompts.
      4. Route through ``execute_routed_model`` (BenTrade's distributed
         model router \u2014 LM Studio / model machine / Bedrock Nova Pro).
      5. Parse the four ``##`` sections out of the response.
      6. Persist the result (incl. failures) to backend/data/eva_analyses/.
    """
    import asyncio

    from app.services.eva_analysis_prompts import (
        ANALYSIS_PROMPT_VERSION,
        SYSTEM_PROMPT,
        build_user_prompt,
        load_cached_analysis,
        parse_structured_sections,
        save_analysis,
        utcnow_iso,
    )
    from app.services.model_routing_integration import (
        RoutingDisabledError,
        execute_routed_model,
    )

    # 1. Cache hit (only if prompt version matches current)
    if not force_refresh:
        cached = load_cached_analysis(event_id)
        if cached is not None and cached.get("prompt_version") == ANALYSIS_PROMPT_VERSION:
            cached["cached"] = True
            return cached
        # Stale (older prompt version) or missing \u2014 fall through and re-analyze.

    # 2. Fetch source data from EVA
    data = await _proxy_get(f"/api/events/{event_id}/analysis-data")
    ticker = data.get("ticker")

    # 3. Build prompts
    system_prompt = SYSTEM_PROMPT
    user_prompt = build_user_prompt(data)

    # 4. Route through the distributed model router (sync \u2014 run in executor)
    def _blocking_call():
        return execute_routed_model(
            task_type="eva_event_analysis",
            messages=[{"role": "user", "content": user_prompt}],
            system_prompt=system_prompt,
            timeout=180.0,
            temperature=0.3,
            metadata={"source": "eva_analysis", "event_id": event_id, "ticker": ticker},
        )

    legacy: dict = {}
    trace = None
    error_message: Optional[str] = None
    try:
        loop = asyncio.get_running_loop()
        legacy, trace = await loop.run_in_executor(None, _blocking_call)
    except RoutingDisabledError as exc:
        error_message = f"Model routing is disabled: {exc}"
        _log.warning("event=eva_analysis_routing_disabled event_id=%s", event_id)
    except Exception as exc:  # noqa: BLE001 \u2014 surface to client
        error_message = f"{type(exc).__name__}: {exc}"
        _log.error("event=eva_analysis_failed event_id=%s error=%s", event_id, exc)

    response_text = (legacy or {}).get("content") or ""
    structured = parse_structured_sections(response_text)

    if error_message is None and (legacy or {}).get("status") != "success":
        error_message = (legacy or {}).get("error") or "Model call returned non-success status"

    raw = (legacy or {}).get("raw_response") or {}
    usage = (raw.get("usage") if isinstance(raw, dict) else None) or {}

    # Resolve a stable execution_mode label.  Trace carries the most
    # accurate value (after override resolution); fall back to None.
    execution_mode = None
    model_provider = None
    if trace is not None:
        execution_mode = getattr(trace, "resolved_mode", None) or getattr(trace, "requested_mode", None)
        model_provider = getattr(trace, "selected_provider", None)
    if not model_provider:
        model_provider = (legacy or {}).get("provider")

    result: dict = {
        "event_id": event_id,
        "ticker": ticker,
        "model_used": (legacy or {}).get("model_name") or model_provider,
        "model_provider": model_provider,
        "execution_mode": execution_mode,
        "prompt_version": ANALYSIS_PROMPT_VERSION,
        "response_text": response_text,
        "structured_sections": structured,
        "tokens_input": usage.get("prompt_tokens"),
        "tokens_output": usage.get("completion_tokens"),
        "created_at": utcnow_iso(),
        "cached": False,
        "error_message": error_message,
    }

    save_analysis(event_id, result)
    return result


# ── Premium model prompt (deterministic, no LLM call) ────────────────

class PremiumPromptOut(BaseModel):
    event_id: int
    ticker: str | None = None
    prompt_text: str
    generated_at: str
    char_count: int


class PremiumResponseIn(BaseModel):
    response_text: str


class PremiumResponseOut(BaseModel):
    saved: bool
    event_id: int
    saved_at: str
    char_count: int


@router.get("/events/{event_id}/premium-prompt", response_model=PremiumPromptOut)
async def get_premium_prompt(event_id: int):
    """Build a copyable premium-tier prompt from raw EVA data.

    v2.0: No longer requires cached local analysis. Premium model approaches
    data fresh and produces independent analysis with view-decomposition framework.
    """
    from app.services.eva_analysis_prompts import build_premium_prompt, utcnow_iso

    analysis_data = await _proxy_get(f"/api/events/{event_id}/analysis-data")
    prompt_text = build_premium_prompt(analysis_data, cached_analysis=None)

    return PremiumPromptOut(
        event_id=event_id,
        ticker=analysis_data.get("ticker"),
        prompt_text=prompt_text,
        generated_at=utcnow_iso(),
        char_count=len(prompt_text),
    )


@router.post("/events/{event_id}/premium-response", response_model=PremiumResponseOut)
async def post_premium_response(event_id: int, body: PremiumResponseIn):
    """Persist a user-pasted premium model response alongside the local analysis.

    Stored in ``backend/data/eva_premium_responses/{event_id}.json``,
    separate from the local analysis cache (different lifecycle). One
    response per event \u2014 a new POST overwrites the prior one.
    """
    from app.services.eva_analysis_prompts import save_premium_response as _save

    text = (body.response_text or "").strip()
    if not text:
        raise HTTPException(400, detail={"error": "empty_response", "message": "response_text is empty"})

    try:
        payload = _save(event_id, text)
    except OSError as exc:
        raise HTTPException(500, detail={"error": "write_failed", "message": str(exc)})

    return PremiumResponseOut(
        saved=True,
        event_id=event_id,
        saved_at=payload["saved_at"],
        char_count=len(text),
    )


@router.get("/events/{event_id}/premium-response")
async def get_premium_response(event_id: int):
    """Return the saved premium model response for an event, or 404 if none."""
    from app.services.eva_analysis_prompts import load_premium_response

    payload = load_premium_response(event_id)
    if not payload:
        raise HTTPException(404, detail={"error": "not_found", "message": "No saved premium response for this event."})
    return payload


# ── Tickers ──────────────────────────────────────────────────────────

@router.get("/tickers/{ticker}")
async def eva_ticker(ticker: str):
    return await _proxy_get(f"/api/tickers/{ticker.upper()}")


@router.get("/tickers/{ticker}/latest-features")
async def eva_ticker_latest_features(ticker: str):
    return await _proxy_get(f"/api/tickers/{ticker.upper()}/latest-features")


# ── Universe admin ───────────────────────────────────────────────────

@router.get("/universe")
async def eva_universe():
    return await _proxy_get("/api/admin/universe")


@router.get("/universe/all")
async def eva_universe_all(include_inactive: bool = Query(False)):
    return await _proxy_get(
        "/api/admin/universe/all",
        params={"include_inactive": "true" if include_inactive else "false"},
    )


@router.post("/universe/add")
async def eva_universe_add(
    ticker: str = Query(..., min_length=1, max_length=10),
    notes: Optional[str] = Query(None),
):
    params: dict = {"ticker": ticker.upper()}
    if notes:
        params["notes"] = notes
    return await _proxy_post("/api/admin/universe/add", params=params)


@router.post("/universe/remove")
async def eva_universe_remove(ticker: str = Query(..., min_length=1, max_length=10)):
    return await _proxy_post("/api/admin/universe/remove", params={"ticker": ticker.upper()})
