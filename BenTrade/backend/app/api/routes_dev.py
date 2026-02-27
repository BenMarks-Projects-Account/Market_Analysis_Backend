"""Developer snapshot-capture endpoint.

POST /dev/snapshots/capture
    Pull option chains for specified (symbol, expiration) combos and save raw
    snapshots to disk.  Returns the saved file paths and index file path.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from app.utils.snapshot import SnapshotRecorder

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/dev/snapshots", tags=["dev"])


class CaptureRequest(BaseModel):
    symbols: list[str] = Field(..., min_length=1)
    expirations: list[str] = Field(..., min_length=1)
    provider: str = "tradier"


class CaptureResponse(BaseModel):
    trace_id: str
    saved_files: list[dict[str, Any]]
    index_path: str | None
    errors: list[str]


@router.post("/capture", response_model=CaptureResponse)
async def capture_snapshots(body: CaptureRequest, request: Request) -> CaptureResponse:
    """Pull chains for all symbol × expiration combos and save raw snapshots."""
    tradier_client = request.app.state.tradier_client
    snapshot_dir: Path = request.app.state.snapshot_dir
    base_data_service = request.app.state.base_data_service

    # Create a one-shot recorder (always enabled, no symbol filter, no limit)
    recorder = SnapshotRecorder(snapshot_dir, enabled=True)
    errors: list[str] = []

    for symbol in body.symbols:
        sym = symbol.upper()

        # Fetch underlying price once per symbol (for metadata)
        try:
            underlying_price = await base_data_service.get_underlying_price(sym)
        except Exception as exc:
            underlying_price = None
            errors.append(f"{sym}: underlying price unavailable ({exc})")

        for expiration in body.expirations:
            try:
                # Fetch the FULL raw payload (bypasses cache, includes envelope)
                raw_payload = await tradier_client.fetch_chain_raw_payload(
                    sym, expiration, greeks=True,
                )
                recorder.save_chain_response(
                    raw_payload,
                    provider=body.provider,
                    symbol=sym,
                    expiration=expiration,
                    endpoint="/markets/options/chains",
                    request_params={
                        "symbol": sym,
                        "expiration": expiration,
                        "greeks": "true",
                    },
                    underlying_price=underlying_price,
                )
            except Exception as exc:
                errors.append(f"{sym} {expiration}: {exc}")
                logger.warning(
                    "event=snapshot_capture_error symbol=%s expiration=%s error=%s",
                    sym, expiration, str(exc),
                )

    index_path = recorder.write_index()

    return CaptureResponse(
        trace_id=recorder.trace_id,
        saved_files=recorder._saved_files,
        index_path=str(index_path) if index_path else None,
        errors=errors,
    )
