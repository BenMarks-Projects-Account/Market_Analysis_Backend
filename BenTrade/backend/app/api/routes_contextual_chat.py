"""
BenTrade — Contextual Chat Route
==================================

Reusable POST endpoint for context-grounded LLM conversations.
Accepts a context contract, user message, and optional prior chat
history.  Returns the assistant's response in a standardized format.

Route: POST /api/chat/contextual
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

router = APIRouter(tags=["contextual-chat"])


# ── Request / Response models ─────────────────────────────────────────

class ChatMessage(BaseModel):
    role: str = Field(..., pattern=r"^(user|assistant)$")
    content: str


class ContextualChatRequest(BaseModel):
    """Reusable request body for contextual chat."""
    context: dict[str, Any] = Field(
        ..., description="Context contract with context_type, context_title, context_payload, etc."
    )
    message: str = Field(..., min_length=1, max_length=4000, description="Current user message")
    history: list[ChatMessage] = Field(
        default_factory=list,
        description="Prior conversation messages for multi-turn context",
    )


class ContextualChatResponse(BaseModel):
    ok: bool
    assistant_message: str
    context_type: str
    finish_reason: str | None = None
    duration_ms: int
    suggested_followups: list[str] = Field(default_factory=list)


# ── Endpoint ──────────────────────────────────────────────────────────

@router.post("/api/chat/contextual", response_model=ContextualChatResponse)
def contextual_chat(payload: ContextualChatRequest):
    """Context-grounded conversational chat.

    Accepts any valid context_type. The prompt assembly framework
    automatically selects the correct context wrapper and builds the
    model messages from the provided context + history.

    NOTE: This is a sync `def` handler (not `async def`) because
    execute_chat() calls blocking I/O (requests.post to the model).
    FastAPI automatically runs sync handlers in a thread-pool,
    keeping the event loop free for other connections.
    """
    from app.services.contextual_chat_service import validate_context, execute_chat

    # Validate context contract
    errors = validate_context(payload.context)
    if errors:
        raise HTTPException(
            status_code=422,
            detail={"message": "Invalid context contract", "errors": errors},
        )

    # Cap history to prevent prompt overflow (keep last 20 turns)
    history = [{"role": m.role, "content": m.content} for m in payload.history[-20:]]

    try:
        result = execute_chat(
            context=payload.context,
            user_message=payload.message,
            chat_history=history,
        )
    except Exception as exc:
        logger.exception("[CONTEXTUAL_CHAT] execution failed: %s", exc)
        # Distinguish transport-layer errors from general failures
        err_name = type(exc).__name__
        is_transport = err_name in (
            "LocalModelUnavailableError", "ConnectionError", "Timeout",
            "ReadTimeout", "ConnectTimeout",
        )
        detail = (
            "AI model is currently unreachable. Please try again shortly."
            if is_transport
            else f"Chat execution failed: {exc}"
        )
        raise HTTPException(status_code=502, detail=detail) from exc

    return result


@router.get("/api/chat/starters/{context_type}")
async def get_quick_starters(context_type: str):
    """Return quick-starter prompts for a given context type."""
    from app.services.contextual_chat_service import QUICK_STARTERS

    starters = QUICK_STARTERS.get(context_type, [])
    return {"context_type": context_type, "starters": starters}
