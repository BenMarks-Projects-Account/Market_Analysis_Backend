"""Proxy routes for Company Evaluator service on Machine 2."""

import logging

import httpx
from fastapi import APIRouter, HTTPException, Query

from app.config import get_settings

_log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/company-evaluator", tags=["company-evaluator"])

_TIMEOUT = 30


def _base_url() -> str:
    return get_settings().COMPANY_EVALUATOR_URL.rstrip("/")


@router.get("/ranked")
async def get_ranked_companies(
    limit: int = Query(50, ge=1, le=500),
    sector: str = Query(None),
    min_score: float = Query(None),
):
    """Proxy: Get ranked companies from the evaluator service."""
    params: dict = {"limit": limit}
    if sector:
        params["sector"] = sector
    if min_score is not None:
        params["min_score"] = min_score

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
