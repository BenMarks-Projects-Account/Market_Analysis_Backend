"""Proxy routes for Company Evaluator service (local or remote)."""

import logging

import httpx
from fastapi import APIRouter, HTTPException, Query

from app.config import get_settings

_log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/company-evaluator", tags=["company-evaluator"])

_TIMEOUT = 30

# Catch both immediate TCP refusal and connection timeouts (e.g. firewall drops)
_CE_CONNECT_ERRORS = (httpx.ConnectError, httpx.TimeoutException)


def _unavailable_detail() -> str:
    """503 detail that includes the target URL for debuggability."""
    return f"Company Evaluator service unavailable at {_base_url()}"


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

_evaluator_connection_mode: str = "remote"


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
    except _CE_CONNECT_ERRORS:
        raise HTTPException(503, detail=_unavailable_detail())
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
    except _CE_CONNECT_ERRORS:
        raise HTTPException(503, detail=_unavailable_detail())
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
    except _CE_CONNECT_ERRORS:
        return {"service_healthy": False, "pipeline": None, "error": f"Not reachable at {_base_url()}"}
    except Exception as exc:
        return {"service_healthy": False, "error": str(exc)}


@router.post("/evaluate/{symbol}")
async def trigger_evaluation(symbol: str):
    """Proxy: Trigger evaluation for a single company."""
    try:
        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(f"{_base_url()}/api/pipeline/evaluate/{symbol.upper()}")
            return resp.json()
    except _CE_CONNECT_ERRORS:
        raise HTTPException(503, detail=_unavailable_detail())
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
    except _CE_CONNECT_ERRORS:
        raise HTTPException(503, detail=_unavailable_detail())
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
    except _CE_CONNECT_ERRORS:
        raise HTTPException(503, detail=_unavailable_detail())
    except Exception as exc:
        _log.error("event=evaluator_proxy_failed error=%s", exc)
        raise HTTPException(500, detail=str(exc))


# ── Proxy routes for valuation / quote / universe (remote-safe) ────────

@router.get("/entry-point/analysis/{symbol}")
async def get_entry_point_analysis(symbol: str):
    """Proxy: Get cached entry-point analysis for a symbol."""
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.get(f"{_base_url()}/api/entry-point/analysis/{symbol.upper()}")
            if resp.status_code != 200:
                raise HTTPException(resp.status_code, detail=resp.text[:300])
            return resp.json()
    except _CE_CONNECT_ERRORS:
        raise HTTPException(503, detail=_unavailable_detail())
    except HTTPException:
        raise
    except Exception as exc:
        _log.error("event=entry_point_get_proxy_failed symbol=%s error=%s", symbol, exc)
        raise HTTPException(500, detail=str(exc))


@router.get("/quote/{symbol}")
async def get_quote(symbol: str):
    """Proxy: Get price quote for a symbol."""
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.get(f"{_base_url()}/api/quote/{symbol.upper()}")
            if resp.status_code != 200:
                raise HTTPException(resp.status_code, detail=resp.text[:300])
            return resp.json()
    except _CE_CONNECT_ERRORS:
        raise HTTPException(503, detail=_unavailable_detail())
    except HTTPException:
        raise
    except Exception as exc:
        _log.error("event=quote_proxy_failed symbol=%s error=%s", symbol, exc)
        raise HTTPException(500, detail=str(exc))


@router.get("/valuation/dcf/{symbol}")
async def get_dcf_cached(symbol: str):
    """Proxy: Get cached DCF analysis."""
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.get(f"{_base_url()}/api/valuation/dcf/{symbol.upper()}")
            if resp.status_code != 200:
                raise HTTPException(resp.status_code, detail=resp.text[:300])
            return resp.json()
    except _CE_CONNECT_ERRORS:
        raise HTTPException(503, detail=_unavailable_detail())
    except HTTPException:
        raise
    except Exception as exc:
        _log.error("event=dcf_get_proxy_failed symbol=%s error=%s", symbol, exc)
        raise HTTPException(500, detail=str(exc))


@router.post("/valuation/dcf")
async def run_dcf(body: dict):
    """Proxy: Run DCF analysis for a symbol."""
    symbol = (body.get("symbol") or "").strip().upper()
    if not symbol:
        raise HTTPException(400, detail="symbol is required")
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                f"{_base_url()}/api/valuation/dcf",
                json={"symbol": symbol},
            )
            if resp.status_code != 200:
                raise HTTPException(resp.status_code, detail=resp.text[:300])
            return resp.json()
    except _CE_CONNECT_ERRORS:
        raise HTTPException(503, detail=_unavailable_detail())
    except HTTPException:
        raise
    except Exception as exc:
        _log.error("event=dcf_post_proxy_failed symbol=%s error=%s", symbol, exc)
        raise HTTPException(500, detail=str(exc))


@router.get("/valuation/eva/{symbol}")
async def get_eva_cached(symbol: str):
    """Proxy: Get cached EVA/ROIC analysis."""
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.get(f"{_base_url()}/api/valuation/eva/{symbol.upper()}")
            if resp.status_code != 200:
                raise HTTPException(resp.status_code, detail=resp.text[:300])
            return resp.json()
    except _CE_CONNECT_ERRORS:
        raise HTTPException(503, detail=_unavailable_detail())
    except HTTPException:
        raise
    except Exception as exc:
        _log.error("event=eva_get_proxy_failed symbol=%s error=%s", symbol, exc)
        raise HTTPException(500, detail=str(exc))


@router.post("/valuation/eva")
async def run_eva(body: dict):
    """Proxy: Run EVA/ROIC analysis for a symbol."""
    symbol = (body.get("symbol") or "").strip().upper()
    if not symbol:
        raise HTTPException(400, detail="symbol is required")
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                f"{_base_url()}/api/valuation/eva",
                json={"symbol": symbol},
            )
            if resp.status_code != 200:
                raise HTTPException(resp.status_code, detail=resp.text[:300])
            return resp.json()
    except _CE_CONNECT_ERRORS:
        raise HTTPException(503, detail=_unavailable_detail())
    except HTTPException:
        raise
    except Exception as exc:
        _log.error("event=eva_post_proxy_failed symbol=%s error=%s", symbol, exc)
        raise HTTPException(500, detail=str(exc))


@router.get("/valuation/comps/{symbol}")
async def get_comps_cached(symbol: str):
    """Proxy: Get cached comparable company analysis."""
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.get(f"{_base_url()}/api/valuation/comps/{symbol.upper()}")
            if resp.status_code != 200:
                raise HTTPException(resp.status_code, detail=resp.text[:300])
            return resp.json()
    except _CE_CONNECT_ERRORS:
        raise HTTPException(503, detail=_unavailable_detail())
    except HTTPException:
        raise
    except Exception as exc:
        _log.error("event=comps_get_proxy_failed symbol=%s error=%s", symbol, exc)
        raise HTTPException(500, detail=str(exc))


@router.post("/valuation/comps")
async def run_comps(body: dict):
    """Proxy: Run comparable company analysis for a symbol."""
    symbol = (body.get("symbol") or "").strip().upper()
    if not symbol:
        raise HTTPException(400, detail="symbol is required")
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                f"{_base_url()}/api/valuation/comps",
                json={"symbol": symbol},
            )
            if resp.status_code != 200:
                raise HTTPException(resp.status_code, detail=resp.text[:300])
            return resp.json()
    except _CE_CONNECT_ERRORS:
        raise HTTPException(503, detail=_unavailable_detail())
    except HTTPException:
        raise
    except Exception as exc:
        _log.error("event=comps_post_proxy_failed symbol=%s error=%s", symbol, exc)
        raise HTTPException(500, detail=str(exc))


@router.get("/analyses/status")
async def get_analyses_status():
    """Proxy: Get bulk analysis status (which symbols have cached analyses)."""
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.get(f"{_base_url()}/api/analyses/status")
            if resp.status_code != 200:
                return {}
            return resp.json()
    except _CE_CONNECT_ERRORS:
        return {}
    except Exception:
        return {}


@router.post("/universe/add")
async def add_to_universe(body: dict):
    """Proxy: Add a stock to the evaluator universe."""
    symbol = (body.get("symbol") or "").strip().upper()
    if not symbol:
        raise HTTPException(400, detail="symbol is required")
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(
                f"{_base_url()}/api/universe/add",
                json={"symbol": symbol},
            )
            if resp.status_code != 200:
                raise HTTPException(resp.status_code, detail=resp.text[:300])
            return resp.json()
    except _CE_CONNECT_ERRORS:
        raise HTTPException(503, detail=_unavailable_detail())
    except HTTPException:
        raise
    except Exception as exc:
        _log.error("event=universe_add_proxy_failed symbol=%s error=%s", symbol, exc)
        raise HTTPException(500, detail=str(exc))


@router.get("/companies/{symbol}/raw")
async def get_company_raw(symbol: str):
    """Proxy: Get raw data inspector payload for a company evaluation."""
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.get(f"{_base_url()}/api/companies/{symbol.upper()}/raw")
            if resp.status_code == 404:
                raise HTTPException(404, detail=f"No evaluation found for {symbol.upper()}")
            if resp.status_code != 200:
                raise HTTPException(resp.status_code, detail=resp.text[:300])
            return resp.json()
    except _CE_CONNECT_ERRORS:
        raise HTTPException(503, detail=_unavailable_detail())
    except HTTPException:
        raise
    except Exception as exc:
        _log.error("event=raw_data_proxy_failed symbol=%s error=%s", symbol, exc)
        raise HTTPException(500, detail=str(exc))


@router.get("/admin/fmp-status")
async def proxy_fmp_status():
    """Proxy: Get FMP data source status from the evaluator service."""
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"{_base_url()}/api/admin/fmp-status")
            if resp.status_code != 200:
                raise HTTPException(resp.status_code, detail="FMP status unavailable")
            return resp.json()
    except _CE_CONNECT_ERRORS:
        raise HTTPException(503, detail=_unavailable_detail())
    except HTTPException:
        raise
    except Exception as exc:
        _log.error("event=fmp_status_proxy_failed error=%s", exc)
        raise HTTPException(503, detail="FMP status unavailable")
