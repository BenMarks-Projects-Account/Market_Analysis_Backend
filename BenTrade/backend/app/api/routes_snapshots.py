"""Admin snapshot-capture endpoints.

POST /api/admin/snapshots/capture    — capture a complete offline dataset
GET  /api/admin/snapshots            — list available snapshots
GET  /api/admin/snapshots/{trace_id} — get manifest for a specific snapshot
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/admin/snapshots", tags=["admin", "snapshots"])


# ── Request / Response models ─────────────────────────────────────────────


class SnapshotCaptureRequest(BaseModel):
    strategy_id: str = Field(..., description="e.g. credit_spread, iron_condor")
    symbols: list[str] = Field(..., min_length=1)
    preset_name: str = "balanced"
    data_quality_mode: str = "standard"
    dte_min: int = 3
    dte_max: int = 60
    max_expirations_per_symbol: int = 6
    provider: str = "tradier"
    lookback_days: int = 365


class SnapshotCaptureResponse(BaseModel):
    trace_id: str
    created_at: str
    strategy_id: str
    symbols: list[str]
    expirations_captured: int
    chains_captured: int
    capture_duration_seconds: float | None
    completeness: dict[str, Any]
    output_path: str
    errors: list[str] = Field(default_factory=list)


# ── Endpoints ─────────────────────────────────────────────────────────────


@router.post("/capture", response_model=SnapshotCaptureResponse)
async def capture_snapshot(body: SnapshotCaptureRequest, request: Request):
    """Capture a complete offline dataset for scanner replay."""
    from app.services.snapshot_capture_service import SnapshotCaptureService

    snapshot_dir: Path = request.app.state.snapshot_dir
    base_data_service = request.app.state.base_data_service
    tradier_client = request.app.state.tradier_client
    fred_client = request.app.state.fred_client
    regime_service = getattr(request.app.state, "regime_service", None)

    service = SnapshotCaptureService(
        base_data_service=base_data_service,
        tradier_client=tradier_client,
        fred_client=fred_client,
        snapshot_dir=snapshot_dir,
        regime_service=regime_service,
    )

    try:
        manifest = await service.capture(
            strategy_id=body.strategy_id,
            symbols=body.symbols,
            preset_name=body.preset_name,
            data_quality_mode=body.data_quality_mode,
            dte_min=body.dte_min,
            dte_max=body.dte_max,
            max_expirations_per_symbol=body.max_expirations_per_symbol,
            provider=body.provider,
            lookback_days=body.lookback_days,
        )
    except Exception as exc:
        logger.error("event=snapshot_capture_failed error=%s", exc, exc_info=True)
        return JSONResponse(
            status_code=500,
            content={
                "error": {
                    "code": "CAPTURE_FAILED",
                    "message": str(exc),
                }
            },
        )

    # Compute output path relative to snapshot_dir
    provider_dir = snapshot_dir / body.provider
    try:
        output_rel = str(
            Path(manifest.trace_id)  # just trace_id as marker
        )
    except Exception:
        output_rel = manifest.trace_id

    # Find actual output directory
    from app.services.snapshot_capture_service import SnapshotCaptureService as SCS
    run_dir = SCS.find_snapshot_by_trace_id(
        snapshot_dir, manifest.trace_id, provider=body.provider,
    )

    return SnapshotCaptureResponse(
        trace_id=manifest.trace_id,
        created_at=manifest.created_at,
        strategy_id=manifest.strategy_id,
        symbols=manifest.symbols,
        expirations_captured=manifest.expirations_captured,
        chains_captured=manifest.chains_captured,
        capture_duration_seconds=manifest.capture_duration_seconds,
        completeness=manifest.completeness.model_dump(),
        output_path=str(run_dir) if run_dir else manifest.trace_id,
    )


@router.get("")
async def list_snapshots(
    request: Request,
    strategy_id: str | None = None,
    provider: str = "tradier",
):
    """List available snapshot runs."""
    from app.services.snapshot_capture_service import SnapshotCaptureService

    snapshot_dir: Path = request.app.state.snapshot_dir

    snapshots = SnapshotCaptureService.list_snapshots(
        snapshot_dir, provider=provider, strategy_id=strategy_id,
    )
    return {
        "snapshots": snapshots,
        "count": len(snapshots),
    }


@router.get("/{trace_id}")
async def get_snapshot_manifest(trace_id: str, request: Request):
    """Return the full manifest for a specific snapshot."""
    from app.services.snapshot_capture_service import SnapshotCaptureService

    snapshot_dir: Path = request.app.state.snapshot_dir

    run_dir = SnapshotCaptureService.find_snapshot_by_trace_id(
        snapshot_dir, trace_id,
    )
    if run_dir is None:
        return JSONResponse(
            status_code=404,
            content={
                "error": {
                    "code": "SNAPSHOT_NOT_FOUND",
                    "message": f"No snapshot found for trace_id={trace_id}",
                }
            },
        )

    import json
    manifest_path = run_dir / "snapshot_manifest.json"
    try:
        raw = json.loads(manifest_path.read_text(encoding="utf-8"))
        return raw
    except (json.JSONDecodeError, OSError) as exc:
        return JSONResponse(
            status_code=500,
            content={
                "error": {
                    "code": "MANIFEST_READ_ERROR",
                    "message": str(exc),
                }
            },
        )
