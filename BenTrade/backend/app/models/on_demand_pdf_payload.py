"""On-Demand Evaluator PDF export payload (Phase 1).

Request body for ``POST /api/export/on-demand-pdf``.

Carries everything the backend needs that the browser already has:
    * ``job_id`` — so the backend can re-fetch the cached CE result via the
      existing proxy ``GET /on-demand/jobs/{job_id}/result`` (no new CE run)
    * ``symbol`` — display + filename
    * ``appended_analyses`` — user-pasted deep-research narratives that exist
      only in browser JS state (CE has no knowledge of them)
    * ``user_notes`` — reserved for Phase 2
    * ``display_context`` — account mode + page render timestamp

Non-negotiables:
    * No CE job polling, no new LLM runs — export is read-only.
    * Missing CE sections produce None DocumentModel fields, not 500s.
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class AppendedAnalysis(BaseModel):
    """A single user-pasted deep-research narrative."""
    model_config = ConfigDict(extra="forbid")

    timestamp: datetime
    title: str = Field(..., min_length=1, max_length=200)
    body_md: str = Field(..., min_length=1, max_length=200_000)


class DisplayContext(BaseModel):
    """Browser-side render context snapshot."""
    model_config = ConfigDict(extra="forbid")

    account_mode: Optional[Literal["live", "paper"]] = None
    generated_at_iso: datetime


class OnDemandPdfPayload(BaseModel):
    """Request body for POST /api/export/on-demand-pdf."""
    model_config = ConfigDict(extra="forbid")

    job_id: str = Field(..., min_length=1, max_length=200)
    symbol: str = Field(..., pattern=r"^[A-Z0-9.\-]{1,10}$")
    appended_analyses: list[AppendedAnalysis] = Field(
        default_factory=list, max_length=20
    )
    user_notes: Optional[str] = Field(default=None, max_length=20_000)
    display_context: DisplayContext
    # Phase 2 (Fix 6): client-captured 1Y price chart as base64 PNG payload
    # (no data: prefix). Optional. ~3M chars ≈ 2.25 MB decoded — comfortable
    # margin under the 20 MB document cap.
    chart_png_base64: Optional[str] = Field(default=None, max_length=3_000_000)
