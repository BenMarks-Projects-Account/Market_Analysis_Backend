from datetime import datetime, timezone

from fastapi import APIRouter, Request

from app.models.schemas import HealthResponse

router = APIRouter(prefix="/api/health", tags=["health"])


@router.get("", response_model=HealthResponse)
async def health(request: Request) -> HealthResponse:
    tradier_ok = await request.app.state.tradier_client.health()
    finnhub_ok = await request.app.state.finnhub_client.health()
    polygon_ok = await request.app.state.polygon_client.health()
    fred_ok = await request.app.state.fred_client.health()

    upstream = {
        "tradier": "ok" if tradier_ok else "down",
        "finnhub": "ok" if finnhub_ok else "down",
        "polygon": "ok" if polygon_ok else "down",
        "fred": "ok" if fred_ok else "down",
    }
    return HealthResponse(ok=all(x == "ok" for x in upstream.values()), upstream=upstream)


@router.get("/sources")
async def sources_health(request: Request) -> dict:
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

    return {
        "as_of": now_iso,
        "sources": sources,
    }
