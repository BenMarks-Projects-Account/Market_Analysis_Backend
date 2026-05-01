"""HTTP routes for home-dashboard component-attached notes (v1)."""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.services import notes_service
from app.services.notes_service import (
    InvalidNoteBodyError,
    UnknownSectionError,
)


_LOG = logging.getLogger(__name__)

router = APIRouter(prefix="/api/notes", tags=["notes"])


class AppendNoteRequest(BaseModel):
    body: str = Field(..., min_length=1, max_length=notes_service.MAX_BODY_LEN)


@router.get("/sections/{section_id}")
async def get_section_notes(section_id: str) -> dict:
    notes = await notes_service.list_notes(section_id)
    return {"section_id": section_id, "notes": notes}


@router.post("/sections/{section_id}/append")
async def append_section_note(section_id: str, payload: AppendNoteRequest) -> dict:
    try:
        note = await notes_service.append_note(section_id, payload.body)
    except UnknownSectionError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except InvalidNoteBodyError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"note": note}


@router.delete("/sections/{section_id}/notes/{note_id}")
async def delete_section_note(section_id: str, note_id: str) -> dict:
    deleted = await notes_service.delete_note(section_id, note_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="note not found")
    return {"deleted": True}
