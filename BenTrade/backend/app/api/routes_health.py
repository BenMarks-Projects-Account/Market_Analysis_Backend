import logging
import time
from datetime import datetime, timezone

import httpx
from fastapi import APIRouter, Request

from app.models.schemas import HealthResponse

logger = logging.getLogger("bentrade.routes_health")

router = APIRouter(prefix="/api/health", tags=["health"])

# ── Health probe timeout (seconds) — applies to each API canary call ───
_HEALTH_PROBE_TIMEOUT_S = 3.0

# ── /sources response-level cache ──────────────────────────────────────
# Prevents rapid navigations from firing redundant external probes.
_sources_cache: dict | None = None
_sources_cache_time: float = 0.0
_SOURCES_CACHE_TTL_S = 30  # return cached result for 30s


@router.get("", response_model=HealthResponse)
async def health(request: Request) -> HealthResponse:
    tradier_ok = await request.app.state.tradier_client.health()
    finnhub_ok = await request.app.state.finnhub_client.health()
    polygon_ok = await request.app.state.polygon_client.health()
    fred_ok = await request.app.state.fred_client.health()

    from app.services.model_health_service import check_model_health_async
    model_health = await check_model_health_async()

    upstream = {
        "tradier": "ok" if tradier_ok else "down",
        "finnhub": "ok" if finnhub_ok else "down",
        "polygon": "ok" if polygon_ok else "down",
        "fred": "ok" if fred_ok else "down",
        "model_endpoint": "ok" if model_health["status"] == "healthy" else "down",
    }
    return HealthResponse(ok=all(x == "ok" for x in upstream.values()), upstream=upstream)


@router.get("/sources")
async def sources_health(request: Request) -> dict:
    global _sources_cache, _sources_cache_time

    # Return cached response if fresh (prevents rapid-nav probe storms)
    now = time.monotonic()
    if _sources_cache is not None and (now - _sources_cache_time) < _SOURCES_CACHE_TTL_S:
        return _sources_cache

    try:
        await request.app.state.base_data_service.refresh_source_health_probe()
    except Exception:
        pass

    snapshot = request.app.state.base_data_service.get_source_health_snapshot()
    now_iso = datetime.now(timezone.utc).isoformat()

    source_name_map = {
        "finnhub": "Finnhub",
        "polygon": "Polygon",
        "tradier": "Tradier",
        "fred": "FRED",
    }

    def _to_canonical_status(raw: str | None) -> str:
        value = str(raw or "").strip().lower()
        if value == "green":
            return "ok"
        if value == "red":
            return "down"
        return "degraded"

    sources: list[dict] = []
    for key in ("finnhub", "polygon", "tradier", "fred"):
        item = snapshot.get(key) or {}
        notes: list[str] = []
        message = str(item.get("message") or "").strip()
        if message:
            notes.append(message)
        last_http = item.get("last_http")
        if last_http not in (None, ""):
            notes.append(f"HTTP {last_http}")

        sources.append(
            {
                "name": source_name_map.get(key, key.upper()),
                "status": _to_canonical_status(item.get("status")),
                "latency_ms": None,
                "last_ok": item.get("last_ok_ts"),
                "notes": notes,
            }
        )

    # ── Model Endpoint health (non-blocking — runs in thread) ─────
    from app.services.model_health_service import check_model_health_async

    model_health = await check_model_health_async()

    model_status_mapped = "ok" if model_health["status"] == "healthy" else "down"
    logger.info(
        "[SOURCES_HEALTH] model source=%s endpoint=%s probe_status=%s "
        "mapped_ui=%s error=%s latency=%dms checked_at=%s",
        model_health.get("source_key", "?"),
        model_health.get("endpoint", "?"),
        model_health.get("status"),
        model_status_mapped,
        model_health.get("error"),
        model_health.get("latency_ms", 0),
        model_health.get("checked_at", "?"),
    )

    model_notes: list[str] = []
    model_models = model_health.get("models_loaded") or []
    if model_models:
        model_notes.append(model_models[0])
    if model_health.get("error"):
        model_notes.append(str(model_health["error"]))
    model_notes.append(f"{model_health.get('latency_ms', 0)} ms")

    sources.append(
        {
            "name": "AI Model",
            "status": model_status_mapped,
            "latency_ms": model_health.get("latency_ms"),
            "last_ok": now_iso if model_health["status"] == "healthy" else None,
            "notes": model_notes,
        }
    )

    # ── FMP health (via Company Evaluator admin endpoint) ──────────
    from app.api.routes_company_evaluator import _base_url as _ce_base_url

    fmp_status = "down"
    fmp_notes: list[str] = []
    fmp_latency: int | None = None
    try:
        _fmp_start = time.monotonic()
        async with httpx.AsyncClient(timeout=5.0) as client:
            fmp_resp = await client.get(f"{_ce_base_url()}/api/admin/fmp-status")
        fmp_latency = int((time.monotonic() - _fmp_start) * 1000)
        if fmp_resp.status_code == 200:
            fmp_data = fmp_resp.json()
            if fmp_data.get("enabled"):
                fmp_status = "ok"
                calls = fmp_data.get("calls_today")
                limit = fmp_data.get("rate_limit_per_day")
                if calls is not None and limit is not None:
                    fmp_notes.append(f"{calls}/{limit} calls")
            else:
                fmp_status = "degraded"
                fmp_notes.append("disabled")
        else:
            fmp_notes.append(f"HTTP {fmp_resp.status_code}")
    except Exception as exc:
        fmp_notes.append(str(exc)[:80])

    sources.append(
        {
            "name": "FMP",
            "status": fmp_status,
            "latency_ms": fmp_latency,
            "last_ok": now_iso if fmp_status == "ok" else None,
            "notes": fmp_notes,
        }
    )

    result = {
        "as_of": now_iso,
        "sources": sources,
    }

    # Update cache
    _sources_cache = result
    _sources_cache_time = time.monotonic()

    return result
