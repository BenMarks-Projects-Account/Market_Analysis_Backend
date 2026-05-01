"""PDF export endpoint for the on-demand evaluator dashboard (Phase 1 rewrite).

No Playwright. No CE job polling. Renders via fpdf2 from cached CE result.

Error taxonomy:
    404 JOB_NOT_FOUND  - CE has no record of the job_id.
    502 CE_UNREACHABLE - CE connection/timeout/5xx.
    500 PDF_TOO_LARGE  - rendered PDF exceeded the 20 MB cap.
    500 RENDER_FAILED  - catch-all for unexpected render errors.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

from app.models.on_demand_pdf_payload import OnDemandPdfPayload
from app.services.on_demand_pdf_service import (
    CEJobNotFoundError,
    CEUnreachableError,
    PDFTooLargeError,
    render_on_demand_pdf,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/export", tags=["export"])

_pdf_render_lock = asyncio.Lock()


def _filename_for(symbol: str) -> str:
    stamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    safe_symbol = "".join(c for c in symbol.upper() if c.isalnum() or c in ("-", "."))
    return f"{safe_symbol}_on_demand_{stamp}.pdf"


@router.post("/on-demand-pdf")
async def export_on_demand_pdf(payload: OnDemandPdfPayload):
    """Render a PDF snapshot of the On-Demand Evaluator dashboard."""
    async with _pdf_render_lock:
        try:
            pdf_bytes = await render_on_demand_pdf(payload)
        except CEJobNotFoundError as exc:
            logger.warning(
                "event=pdf.export.job_not_found symbol=%s job_id=%s",
                payload.symbol, payload.job_id,
            )
            raise HTTPException(
                status_code=404,
                detail={
                    "code": "JOB_NOT_FOUND",
                    "message": "Company Evaluator has no cached result for this job. Re-run the analysis and try again.",
                    "job_id": payload.job_id,
                },
            ) from exc
        except CEUnreachableError as exc:
            logger.error(
                "event=pdf.export.ce_unreachable symbol=%s job_id=%s error=%s",
                payload.symbol, payload.job_id, exc,
            )
            raise HTTPException(
                status_code=502,
                detail={
                    "code": "CE_UNREACHABLE",
                    "message": "Company Evaluator is unreachable. Check that the CE backend is running.",
                },
            ) from exc
        except PDFTooLargeError as exc:
            logger.error(
                "event=pdf.export.too_large symbol=%s job_id=%s error=%s",
                payload.symbol, payload.job_id, exc,
            )
            raise HTTPException(
                status_code=500,
                detail={
                    "code": "PDF_TOO_LARGE",
                    "message": "Generated PDF exceeded size limit. Try removing some appended analyses.",
                },
            ) from exc
        except Exception as exc:
            logger.exception(
                "event=pdf.export.render_failed symbol=%s job_id=%s",
                payload.symbol, payload.job_id,
            )
            raise HTTPException(
                status_code=500,
                detail={
                    "code": "RENDER_FAILED",
                    "message": f"PDF generation failed: {exc}",
                },
            ) from exc

    filename = _filename_for(payload.symbol)
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Content-Length": str(len(pdf_bytes)),
        },
    )
