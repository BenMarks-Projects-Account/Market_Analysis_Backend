"""Proxy routes for Company Evaluator service (local or remote)."""

import logging

import httpx
from fastapi import APIRouter, HTTPException, Query

from app.config import get_settings

_log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/company-evaluator", tags=["company-evaluator"])

_TIMEOUT = 30

# ── Connection mode config ─────────────────────────────────────────────
COMPANY_EVALUATOR_CONFIG: dict = {
    "local": {
        "host": "localhost",
        "port": 8100,
        "label": "Local",
    },
    "remote": {
        "host": "192.168.1.143",
        "port": 8100,
        "label": "Remote (Model Machine)",
    },
}

_evaluator_connection_mode: str = "local"


def _get_evaluator_url() -> str:
    """Resolve the evaluator base URL from the active connection mode."""
    cfg = COMPANY_EVALUATOR_CONFIG[_evaluator_connection_mode]
    return f"http://{cfg['host']}:{cfg['port']}"


def _base_url() -> str:
    return _get_evaluator_url()


# ── Connection mode endpoints ──────────────────────────────────────────

@router.get("/connection")
async def get_evaluator_connection():
    """Return current connection mode and resolved URL."""
    cfg = COMPANY_EVALUATOR_CONFIG[_evaluator_connection_mode]
    return {
        "mode": _evaluator_connection_mode,
        "url": f"http://{cfg['host']}:{cfg['port']}",
        "label": cfg["label"],
        "available_modes": {
            k: {"label": v["label"], "url": f"http://{v['host']}:{v['port']}"}
            for k, v in COMPANY_EVALUATOR_CONFIG.items()
        },
    }


@router.post("/connection")
async def set_evaluator_connection(body: dict):
    """Switch the evaluator connection mode (local / remote)."""
    global _evaluator_connection_mode
    mode = body.get("mode")
    if mode not in COMPANY_EVALUATOR_CONFIG:
        raise HTTPException(400, detail=f"Unknown mode: {mode}. Valid: {list(COMPANY_EVALUATOR_CONFIG.keys())}")
    _evaluator_connection_mode = mode
    cfg = COMPANY_EVALUATOR_CONFIG[mode]
    _log.info("event=evaluator_connection_changed mode=%s url=http://%s:%s", mode, cfg["host"], cfg["port"])
    return {
        "ok": True,
        "mode": mode,
        "url": f"http://{cfg['host']}:{cfg['port']}",
        "label": cfg["label"],
    }


@router.get("/ranked")
async def get_ranked_companies(
    limit: int = Query(500, ge=1, le=1000),
    sector: str = Query(None),
    min_score: float = Query(None),
    include_universe: bool = Query(True),
):
    """Proxy: Get ranked companies from the evaluator service.

    ``include_universe=true`` (default) asks the upstream service to
    include universe_symbols fields (market_cap, source/tier) if it
    supports the parameter.  If the upstream ignores the flag, the
    response is returned unmodified.
    """
    params: dict = {"limit": limit}
    if sector:
        params["sector"] = sector
    if min_score is not None:
        params["min_score"] = min_score
    if include_universe:
        params["include_universe"] = "true"

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.get(f"{_base_url()}/api/companies/ranked", params=params)
            if resp.status_code != 200:
                raise HTTPException(resp.status_code, detail="Evaluator service error")
            return resp.json()
    except httpx.ConnectError:
        raise HTTPException(503, detail="Company Evaluator service unavailable (Machine 2 not reachable)")
    except HTTPException:
        raise
    except Exception as exc:
        _log.error("event=evaluator_proxy_failed error=%s", exc)
        raise HTTPException(500, detail=str(exc))


@router.get("/company/{symbol}")
async def get_company_detail(symbol: str):
    """Proxy: Get full evaluation detail for a company."""
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.get(f"{_base_url()}/api/companies/{symbol.upper()}")
            if resp.status_code != 200:
                raise HTTPException(resp.status_code, detail="Company not found or service error")
            return resp.json()
    except httpx.ConnectError:
        raise HTTPException(503, detail="Company Evaluator service unavailable")
    except HTTPException:
        raise
    except Exception as exc:
        _log.error("event=evaluator_proxy_failed error=%s", exc)
        raise HTTPException(500, detail=str(exc))


@router.get("/status")
async def get_evaluator_status():
    """Proxy: Get pipeline status from the evaluator service."""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(f"{_base_url()}/api/pipeline/status")
            health = await client.get(f"{_base_url()}/health")
            return {
                "service_healthy": health.status_code == 200,
                "pipeline": resp.json() if resp.status_code == 200 else None,
            }
    except httpx.ConnectError:
        return {"service_healthy": False, "pipeline": None, "error": "Machine 2 not reachable"}
    except Exception as exc:
        return {"service_healthy": False, "error": str(exc)}


@router.post("/evaluate/{symbol}")
async def trigger_evaluation(symbol: str):
    """Proxy: Trigger evaluation for a single company."""
    try:
        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(f"{_base_url()}/api/pipeline/evaluate/{symbol.upper()}")
            return resp.json()
    except httpx.ConnectError:
        raise HTTPException(503, detail="Company Evaluator service unavailable")
    except Exception as exc:
        _log.error("event=evaluator_proxy_failed error=%s", exc)
        raise HTTPException(500, detail=str(exc))


@router.post("/entry-point/analyze")
async def entry_point_analyze(body: dict):
    """Proxy: Run entry-point analysis for a symbol on the evaluator service."""
    symbol = (body.get("symbol") or "").strip().upper()
    if not symbol:
        raise HTTPException(400, detail="symbol is required")
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                f"{_base_url()}/api/entry-point/analyze",
                json={"symbol": symbol},
            )
            if resp.status_code != 200:
                raise HTTPException(resp.status_code, detail=resp.text[:300])
            return resp.json()
    except httpx.ConnectError:
        raise HTTPException(503, detail="Company Evaluator service unavailable")
    except HTTPException:
        raise
    except Exception as exc:
        _log.error("event=entry_point_proxy_failed symbol=%s error=%s", symbol, exc)
        raise HTTPException(500, detail=str(exc))


@router.post("/crawl")
async def trigger_crawl(full_universe: bool = True):
    """Proxy: Trigger full universe crawl on Machine 2."""
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{_base_url()}/api/pipeline/run",
                params={"full_universe": full_universe},
            )
            return resp.json()
    except httpx.ConnectError:
        raise HTTPException(503, detail="Company Evaluator service unavailable")
    except Exception as exc:
        _log.error("event=evaluator_proxy_failed error=%s", exc)
        raise HTTPException(500, detail=str(exc))
